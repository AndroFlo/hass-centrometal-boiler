# CLAUDE.md

Intégration custom Home Assistant pour la chaudière Centrometal **BioTec-Plus** (aussi vendue
sous le nom Morvan GMX EASY, type Centrometal `biopl`) équipée d'une CM WiFi-Box. Le support des
autres modèles (PelTec, Compact, CM Pelet-set, BioTec-L) a été retiré : seules les chaudières
renvoyées par `common.py::supported_devices` reçoivent des entités, les autres sont ignorées.
Fork de `9a4gl/hass-centrometal-boiler` (origin = `AndroFlo/hass-centrometal-boiler`), distribué via HACS.

## Architecture

Tout le code vit dans `custom_components/centrometal_boiler/`. Les tests sans dépendance
(`tests/`) vérifient les fichiers de configuration et les invariants des tables de capteurs ;
ils tournent sans Home Assistant ni compte Centrometal. La CI (`.github/workflows/`) lance la
validation HACS, `hassfest`, les tests, et refuse une PR touchant `custom_components/` sans
bump de `manifest.json`.

La connexion au cloud Centrometal est entièrement déléguée à la librairie externe
`py-centrometal-web-boiler` — fork `AndroFlo`, publié sur PyPI sous `py-centrometal-web-boiler-androflo` (pin dans `manifest.json` → `requirements`). L'intégration ne parle
jamais HTTP/WebSocket directement : elle consomme `WebBoilerClient`.

### Flux de données

1. `config_flow.py` — saisie e-mail / mot de passe (+ préfixe optionnel), valide via `try_connection`.
   L'unique_id de l'entrée est l'e-mail.
2. `__init__.py` / `WebBoilerSystem` — login, `get_configuration()`, puis `start_websocket()`.
   Stocke le client et le système dans `hass.data[DOMAIN][email][WEB_BOILER_CLIENT | WEB_BOILER_SYSTEM]`.
3. Une boucle `tick()` re-planifiée chaque seconde via `async_call_later` gère la résilience :
   relogin si le websocket est tombé (`WEB_BOILER_LOGIN_RETRY_INTERVAL` = 60 s), `refresh()`
   périodique sinon (`WEB_BOILER_REFRESH_INTERVAL` = 600 s).
4. Les plateformes (`sensor`, `switch`, `binary_sensor`, `button`) itèrent sur `web_boiler_client.data.values()`
   — un `device` par chaudière — et construisent les entités.

### Modèle push, jamais de polling

Toutes les entités renvoient `should_poll = False`. La mise à jour passe par
`parameter.set_update_callback(self.update_callback, "<tag>")` posé dans `async_added_to_hass`,
et le callback appelle `async_write_ha_state()`. `available` reflète
`web_boiler_client.is_websocket_connected()`.

Le désabonnement se fait dans `async_will_remove_from_hass`, **jamais dans `__del__`** : le
callback stocke une méthode liée dans le `parameter`, qui vit dans `hass.data`, donc l'entité
n'est jamais collectée tant que l'abonnement est actif et `__del__` ne se déclencherait pas.

Le tag est **unique par entité** (`self._callback_tag`, dérivé de l'`unique_id`). Un tag
littéral partagé ferait qu'une entité écrase silencieusement le callback d'une autre abonnée
au même paramètre.

### Paramètres device

Un `device` est un dict-like exposant `get_parameter(name)` / `has_parameter(name)`. Chaque
paramètre est repéré par un code brut de la chaudière (`B_STATE`, `B_Tak1_1`, `B_razP`,
`PVAL_<dbindex>_0`…). Marquer `parameter["used"] = True` est important : `create_unknown_entities`
crée en fin de parcours un capteur `{?} <code>` pour tout paramètre non consommé, désactivé et
invisible par défaut. C'est le mécanisme de découverte des codes non encore mappés.

### Tables de capteurs

Les capteurs « génériques » sont déclarés en tables déclaratives : `sensors/generic_sensors_all.py`
(communs) et `sensors/generic_sensors_biotec_plus.py` (BioTec-Plus).
Format de valeur — une liste positionnelle, pas un dict :

```python
"B_Tak1_1": [unit, icon, device_class, description, attributes?]
```

`attributes` (5ᵉ élément, optionnel) mappe d'autres codes paramètres vers des libellés affichés
en `extra_state_attributes` ; ces paramètres sont eux aussi marqués `used`.

Ajouter le support d'une valeur revient normalement à ajouter une ligne dans la bonne table —
pas à écrire une classe. Une classe dédiée dans `sensors/` n'est justifiée que pour une logique
de transformation (`WebBoilerPelletLevelSensor`, `WebBoilerWorkingTableSensor`,
`WebBoilerFuelPercentageSensor` qui gère l'arrivée tardive de `B_razP`…) ; elle hérite alors de
`WebBoilerGenericSensor` et surcharge `native_value` / `create_entities`.

### Cycle de vie de l'entrée

`async_setup_entry` lève `ConfigEntryAuthFailed` si la connexion échoue, ce qui déclenche le
flux de ré-authentification plutôt que de laisser une intégration vide. La boucle `tick`
(relogin/refresh) se replanifie elle-même : son handle est conservé pour qu'`async_unload_entry`
puisse l'annuler, sinon elle survivrait à la suppression de l'entrée. Tout ce qui doit être
libéré est empilé dans `WEB_BOILER_UNSUBSCRIBE`.

L'`OptionsFlow` permet de changer les options de nommage sans recréer l'entrée.

### Nommage des entités

`common.py::format_name` centralise la règle, à ne pas contourner :
- préfixe par le n° de série si plusieurs chaudières sur le compte ;
- préfixe utilisateur optionnel (`CONF_PREFIX`) ;
- le nom du produit est préfixé ou non selon l'option `product_prefix` (booléen du config flow).

`unique_id` est toujours dérivé du n° de série (`f"{serial}-{parameter_name}"`) — ne jamais le
changer pour une entité existante, cela casserait les installations en place.
`create_device_info` regroupe toutes les entités sous un même device HA identifié par le n° de série.

## Conventions

- Support de compatibilité déclaré : HA `2024.11.2` (`hacs.json`).
- Le code applicatif et les libellés d'entités sont en **anglais** (la langue de l'intégration
  upstream). Seuls les échanges avec l'utilisateur de cette session sont en français.
- Les nouvelles chaînes du config flow vont à la fois dans `strings.json` et `translations/en.json`.
- Pas de formateur configuré ; le style est proche de Black. Suivre le fichier voisin.

## Publier une modification

Toute modification fonctionnelle doit s'accompagner d'un bump de `version` dans
`manifest.json` — c'est ce champ que HACS lit pour proposer la mise à jour.

Pour publier, pousser un tag égal à la `version` du manifest (`git tag 0.1.1 && git push origin
0.1.1`) : `.github/workflows/release.yml` vérifie la correspondance et crée la release GitHub,
qu'HACS propose comme version. Utiliser des versions sans suffixe : un tag suffixé
(`0.1.1-beta.1`) devient une pré-release, que HACS ne propose qu'avec « Show beta versions ».
Le workflow `version-bump.yml` refuse une PR qui touche `custom_components/` sans ce bump.
Si le changement dépend d'une évolution de la librairie, bumper aussi le pin
`py-centrometal-web-boiler-androflo==0.0.x` dans `requirements` (la version doit déjà être sur PyPI).

Les entités n'exposent aucun service : la chaudière et ses circuits se pilotent via les
services standard `switch.turn_on` / `switch.turn_off`. Ne pas documenter de service
`centrometal_boiler.*` sans l'enregistrer réellement.

## Tester

`python -m pytest tests/` — ne demande que `pytest`, et couvre les fichiers de configuration
(manifest, alignement des traductions) et les invariants des tables de capteurs.

Le comportement runtime (websocket, entités, config flow) demande en revanche une instance
Home Assistant connectée à un vrai compte Centrometal.

## Débogage

Pour tracer, dans le `configuration.yaml` de HA :

```yaml
logger:
  logs:
    custom_components.centrometal_boiler: debug
    centrometal_web_boiler: debug
```
