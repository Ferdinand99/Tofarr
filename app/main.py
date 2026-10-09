from app.config import Settings
from app.db import Db
from app.scheduler import Scheduler
from app.seeds import seed_defaults
from app.tofa.client import TofaClient
from app.web.main import create_app

settings = Settings.from_env()
db = Db(settings.config_dir / "tofa-collections.db")
seed_defaults(db)
make_tofa =lambda: TofaClient(settings.tofa_url, settings.tofa_api_key)
scheduler = Scheduler(db, make_tofa, settings)
scheduler.start()
make_probe = lambda: TofaClient(settings.tofa_url, settings.tofa_api_key, retries=1, timeout=3)
app = create_app(settings, db, make_tofa, scheduler, probe_factory=make_probe)
