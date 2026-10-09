import re
from app.models import SourceItem

class ManualSource:
    def __init__(self, cfg: dict):
        self.text = cfg.get("text", "")

    def fetch(self) -> list[SourceItem]:
        out = []
        for line in self.text.splitlines():
            line = line.split("#", 1)[0].strip()
            m = re.fullmatch(r"(\d+)(?:\s+(movie|tv))?", line)
            if m:
                out.append(SourceItem(int(m.group(1)), m.group(2) or "movie"))
        return out
