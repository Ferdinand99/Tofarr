from app.config import Settings
from app.db import Db
from app.scheduler import Scheduler
from app.tofa.client import TofaClient
from app.web.main import create_app

settings = Settings.from_env()
db = Db(settings.config_dir / "tofa-collections.db")
make_tofa = lambda: TofaClient(settings.tofa_url, settings.tofa_api_key)
scheduler = Scheduler(db, make_tofa, settings)
scheduler.start()
app = create_app(settings, db, make_tofa, scheduler)
