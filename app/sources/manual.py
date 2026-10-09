import re
from app.models import SourceItem

class ManualSource:
    def __init__(self, cfg: dict):
        self.text = cfg.get("text", "")

    def fetch(self) -> list[SourceItem]:
        out = []
        for line in self.text.splitlines():
            head, _, comment = line.partition("#")  # the comment doubles as the title shown in previews
            m = re.fullmatch(r"(\d+)(?:\s+(movie|tv))?", head.strip())
            if m:
                out.append(SourceItem(int(m.group(1)), m.group(2) or "movie", comment.strip()))
        return out
