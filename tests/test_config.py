from app.config import Settings

def test_from_env_strips_trailing_slash(monkeypatch, tmp_path):
    monkeypatch.setenv("TOFA_URL", "http://10.0.0.5:33333/")
    monkeypatch.setenv("TOFA_API_KEY", "k")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    s = Settings.from_env()
    assert s.tofa_url == "http://10.0.0.5:33333"
    assert s.tmdb_api_key is None
    assert s.config_dir == tmp_path
