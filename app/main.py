from app.config import LiveSettings, Settings
from app.db import Db
from app.scheduler import Scheduler
from app.seeds import seed_defaults
from app.tofa.client import TofaClient
from app.web.main import create_app

env = Settings.from_env()
db = Db(env.config_dir / "tofa-collections.db")
settings = LiveSettings(env, db)  # UI-saved values override env defaults, read on every use
seed_defaults(db)
make_tofa = lambda: TofaClient(settings.tofa_url, settings.tofa_api_key)
make_probe = lambda: TofaClient(settings.tofa_url, settings.tofa_api_key, retries=1, timeout=3)
scheduler = Scheduler(db, make_tofa, settings)
scheduler.start()
app = create_app(settings, db, make_tofa, scheduler, probe_factory=make_probe)
