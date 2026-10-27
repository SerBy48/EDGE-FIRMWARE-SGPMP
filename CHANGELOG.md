## [0.3.0](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/compare/v0.2.0...v0.3.0) (2026-10-27)

### Features

* catálogo de sensores con detección por captura (MPU6050, MAX30102) ([78a4190](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/commit/78a4190ef8e366612c8f629c783cdd2095da587c))
* **edge_agent:** almacen local de reglas de RF-55 ([257eda5](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/commit/257eda5cdbaa1f661c6fc2f2f412c2eeb28f8e6b))
* **edge_agent:** evaluar lecturas contra umbrales y clasificar desviaciones (RF-55.1) ([04c90c2](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/commit/04c90c26d6bb4b25cd2ba8578706882657ef916c))
* **edge_agent:** perfil de hardware que habilita o deshabilita RF-55.2 ([7f3b79d](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/commit/7f3b79d1589bed4d4071f21065399dbb3ee9965e))
* **edge_agent:** publicar eventos Edge en el topic evento con buffer offline (RF-55) ([44d73af](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/commit/44d73af4ce84547a4f98ca158c61afddcd1a95d9))
* **edge_agent:** recibir reglas de RF-55 y confirmar con ACK_REGLA ([823849e](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/commit/823849e8c9ac0b29501046d8ba955e7f30f6ca52))
* **edge_agent:** reglas compuestas con ventana de confirmacion y formula THI (RF-55.2) ([08b05b2](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/commit/08b05b2f6b2ba037b6d5b733e9dce8be0d3e0ca2))
* **lora:** protocolo v2 cifrado con identidad por serial ([13e74af](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/commit/13e74af885b851da5618a1277210d4eb9cf1b51a))

### Bug Fixes

* **edge_agent:** version_firmware en SemVer 2.0 comparable por el backend (FW-06) ([6dd61f1](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/commit/6dd61f167736482a82a37f01e948e9ee9441bc04))
* **esp8266:** diagnóstico del SX1276 y lectura del DS18B20 por SKIP_ROM ([d9daeac](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/commit/d9daeac6df53ff7ab882ee63934277af9a9b3f43))
* **esp8266:** no reiniciar si el SX1276 devuelve registros incoherentes ([64e1159](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/commit/64e1159a76e751b032fd0d4de4dc4052b330cb5d))

## [0.2.0](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/compare/v0.1.1...v0.2.0) (2026-10-22)

### Features

* **esp8266:** nodo sensor en NodeMCU con SX1276, DHT22 y DS18B20 ([7b57d0a](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/commit/7b57d0aa45d88eaa2f374e98d48f38da85353f56))
* **raspberry:** script de diagnóstico del SX1276 sin transmitir ([f67d2f3](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/commit/f67d2f3cb2225ed71158e657f15a7a63bd045392))

## [0.1.1](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/compare/v0.1.0...v0.1.1) (2026-10-10)

### Bug Fixes

* **edge_agent:** el updater ignora los release candidates (vX.Y.Z-rc.N) ([c3a1dbb](https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP/commit/c3a1dbb0f31cae9130021f3143ed8348f5f721e2))
