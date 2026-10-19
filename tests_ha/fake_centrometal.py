"""A local imitation of web-boiler.com (website + STOMP broker).

Adapted from the library's tests (py-centrometal-web-boiler/tests/fake_server.py): only the
behaviour the integration relies on is reproduced, with the shapes of the real payloads.
"""

import asyncio
import json

import stomper
from aiohttp import web
from websockets.asyncio.server import serve

EMAIL = "user@example.com"
PASSWORD = "secret"
CSRF = "csrf-123"


def boiler(device_id, serial, device_type, product, params):
    """One installation of the account and its current parameter values."""
    return {
        "installation": {
            "value": device_id,
            "label": serial,
            "place": "Home",
            "address": "1 Main Street",
            "type": device_type,
            "product": product,
        },
        "status": {
            "installation": {"country": "France", "countryCode": "FR"},
            "params": {name: {"v": value, "ut": "2026-09-30 10:00:00"} for name, value in params.items()},
        },
        "parameter_list": {
            "city": "Lyon",
            "parameters": [
                {"group": "Heating circuits", "list": [{"naslov": "Circuit 1", "dbindex": 72}]},
            ]
            if device_type == "biopl"
            else [],
        },
    }


BIOTEC = boiler(
    4242,
    "ABC123",
    "biopl",
    "GMX EASY",
    {
        "B_STATE": "ON",
        "B_pbs": "0",
        "B_scs": "1",
        "B_Tk1b": "65",
        "PVAL_72_0": "1",
        "PMIN_72_0": "0",
        "PMAX_72_0": "1",
        "PDEF_72_0": "1",
    },
)
PELTEC = boiler(777, "PEL999", "peltec", "PelTec", {"B_STATE": "ON"})

# A BioTec-Plus reporting a buffer tank, for the relight prediction. Kept separate
# from BIOTEC so the other tests keep the exact set of entities they assert on.
BIOTEC_BUFFER = boiler(
    4242,
    "ABC123",
    "biopl",
    "GMX EASY",
    {
        "B_STATE": "ON",
        "B_pbs": "0",
        "B_scs": "1",
        "B_Tk1b": "65",
        # Buffer tank probes and everything the prediction model reads
        "B_Tak1_1": "70",
        "B_Tak2_1": "50",
        "B_Tva1": "0",
        "B_Tdpl1": "25",
        "B_Tlo1": "25",
        "B_P1": "0",
        "B_zahP1": "0",
        "C1B_Tpol": "45",
        "C1B_Tsob": "21",
        "C1B_P": "1",
        "PVAL_72_0": "1",
        "PMIN_72_0": "0",
        "PMAX_72_0": "1",
        "PDEF_72_0": "1",
    },
)


class FakeWebsite:
    """aiohttp application recording the commands it receives."""

    def __init__(self, boilers):
        self.boilers = boilers
        self.commands: list[tuple[str, dict]] = []
        self.command_status = "success"
        self.down = False  # every page answers 503, like during a Centrometal outage
        app = web.Application()
        app.router.add_get("/login", self.login_page)
        app.router.add_post("/login_check", self.login_check)
        app.router.add_post("/data/autocomplete/installation", self.installations)
        app.router.add_post("/api/configuration", self.json({}))
        app.router.add_post("/api/widgets-grid/list", self.json({"selected": 7}))
        app.router.add_post("/api/widgets-grid", self.json({"grid": "{}"}))
        app.router.add_post("/wdata/data/installation-status-all", self.statuses)
        app.router.add_post("/wdata/data/parameter-list/{serial}", self.parameter_list)
        app.router.add_post("/notifications/data/get", self.login_page)
        app.router.add_post("/api/inst/control/multiple", self.control)
        app.router.add_post("/api/inst/control/{id}", self.control)
        self.app = app

    @staticmethod
    def json(payload):
        async def handler(request):
            return web.json_response(payload)

        return handler

    async def installations(self, request):
        return web.json_response({"installations": [b["installation"] for b in self.boilers]})

    async def statuses(self, request):
        return web.json_response(
            {str(b["installation"]["value"]): b["status"] for b in self.boilers}
        )

    async def parameter_list(self, request):
        serial = request.match_info["serial"]
        for b in self.boilers:
            if b["installation"]["label"] == serial:
                return web.json_response(b["parameter_list"])
        return web.Response(status=404)

    async def login_page(self, request):
        if self.down:
            return web.Response(status=503, text="maintenance")
        return web.Response(
            text=f'<html><input type="hidden" name="_csrf_token" value="{CSRF}" /></html>',
            content_type="text/html",
        )

    async def login_check(self, request):
        form = await request.post()
        ok = form["_username"] == EMAIL and form["_password"] == PASSWORD
        body = '<div id="id-loading-screen-blackout"></div>' if ok else "<form>login</form>"
        return web.Response(text=f"<html><body>{body}</body></html>", content_type="text/html")

    async def control(self, request):
        self.commands.append((request.path, await request.json()))
        return web.json_response({"status": self.command_status})


class FakeBroker:
    """Minimal STOMP broker. delay: seconds before accepting a connection, like the real cloud."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.connections = []
        self.server = None
        # destination -> [(websocket, subscription id)], filled by the SUBSCRIBE
        # frames, so publish() can push values the way the real boiler does.
        self.subscriptions: dict[str, list] = {}

    async def start(self) -> str:
        self.server = await serve(self.handler, "127.0.0.1", 0)
        return f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/ws"

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def handler(self, websocket):
        await asyncio.sleep(self.delay)
        self.connections.append(websocket)
        try:
            async for message in websocket:
                if message == "\n":
                    continue
                frame = stomper.unpack_frame(message)
                if frame["cmd"] == "CONNECT":
                    await websocket.send("CONNECTED\nversion:1.2\nheart-beat:0,0\n\n\x00")
                elif frame["cmd"] == "SUBSCRIBE":
                    headers = frame["headers"]
                    destination = headers.get("destination", "")
                    self.subscriptions.setdefault(destination, []).append(
                        (websocket, headers.get("id", "0"))
                    )
        except Exception:
            pass  # connection dropped by drop_connections()

    async def publish(self, serial, params, device_type="biopl"):
        """Push new parameter values, the way a real boiler does.

        The integration only applies parameters it already knows (the library
        ignores names absent from the initial HTTPS snapshot), so these must exist
        in the boiler fixture.
        """
        destination = f"/topic/cm.inst.{device_type}.{serial}"
        body = json.dumps(params)
        for websocket, subscription in self.subscriptions.get(destination, []):
            frame = (
                f"MESSAGE\nsubscription:{subscription}\ndestination:{destination}\n"
                f"content-type:application/json\n\n{body}\x00"
            )
            try:
                await websocket.send(frame)
            except Exception:
                pass  # a connection dropped mid-test

    async def drop_connections(self):
        for websocket in self.connections:
            await websocket.close(code=1011, reason="broker restart")
        self.connections.clear()
        self.subscriptions.clear()
