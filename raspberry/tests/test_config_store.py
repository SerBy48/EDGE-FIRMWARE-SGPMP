from edge_agent.config_store import ConfigStore, DeviceConfig

DEFAULTS = DeviceConfig(frecuencia_captura_min=10, intervalo_transmision_min=15)


def test_sobrevive_reinicio(tmp_path):
    path = tmp_path / "config.json"
    ConfigStore(path, DEFAULTS).set("IOT-A", DeviceConfig(5, 30, comando_id="c-1"))

    reloaded = ConfigStore(path, DEFAULTS)
    assert reloaded.get("IOT-A") == DeviceConfig(5, 30, comando_id="c-1")
    assert reloaded.get("IOT-B") == DEFAULTS


def test_archivo_corrupto_arranca_con_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{no es json")

    store = ConfigStore(path, DEFAULTS)
    assert store.get("IOT-A") == DEFAULTS
    assert (tmp_path / "config.json.corrupt").exists()
