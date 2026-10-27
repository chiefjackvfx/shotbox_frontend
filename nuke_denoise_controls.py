"""Scale-aware matching of controls captured from Windows Neat Video 6.1.3."""
from pathlib import Path

import cv2
import numpy as np


class WindowsControlMatcher:
    # Disabled controls share most pixels with enabled controls. Compare their
    # actual captured appearance at the same location, not just a loose score.
    alternatives = {
        "auto_profile": "build_profile", "build_profile": "auto_profile",
        "adjust_ready": "adjust_disabled", "apply": "apply_disabled",
        "profile_check": "profile_check_active", "profile_check_active": "profile_check",
    }
    threshold = .012

    def __init__(self):
        folder = Path(__file__).parent / "ui/denoise/windows"
        names = set(self.alternatives) | set(self.alternatives.values()) | {"profile_check_preview"}
        self.templates = {name: cv2.imread(str(folder / f"{name}.png")) for name in names}
        if any(value is None for value in self.templates.values()):
            raise RuntimeError("Windows Neat Video control references are missing or unreadable.")
        self.scale = None
        self.frame = None
        self.results = {}
        self.resized = {}

    def _template(self, name, scale):
        source = self.templates[name]
        size = (max(1, round(source.shape[1] * scale)), max(1, round(source.shape[0] * scale)))
        key = name, size
        if key not in self.resized:
            pixels = cv2.resize(source, size, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
            mask = None
            if name == "profile_check_preview":
                # Match the Assist Image lettering/outline, excluding footage.
                mask = np.where(np.all(source >= 220, axis=2) | np.all(source <= 40, axis=2), 255, 0).astype(np.uint8)
                mask = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
            self.resized[key] = pixels, mask
        return self.resized[key]

    def _search(self, region, name, scales):
        best = None
        for scale in scales:
            template, mask = self._template(name, float(scale))
            h, w = template.shape[:2]
            if region.shape[0] < h or region.shape[1] < w:
                continue
            scores = cv2.matchTemplate(region, template, cv2.TM_SQDIFF_NORMED, mask=mask)
            scores[~np.isfinite(scores)] = 1
            score, _, point, _ = cv2.minMaxLoc(scores)
            if best is None or score < best[0]:
                best = (score, point, float(scale), w, h)
        return best

    def match(self, pixels, name):
        if self.frame is not pixels:
            self.frame, self.results = pixels, {}
        if name in self.results:
            return self.results[name]
        offset = round(pixels.shape[0] * .88) if name == "apply" else 0
        region = pixels[offset:] if name == "apply" else pixels[:round(pixels.shape[0] * .22)]
        # Calibrate from Auto Profile, then keep all controls at that scale.
        # This prevents a disabled label matching an unrelated tiny variant.
        scales = (np.arange(.5, 2.01, .05) if self.scale is None
                  else np.arange(max(.5, self.scale - .01), min(2, self.scale + .01) + .001, .005))
        best = self._search(region, name, scales)
        if best and self.scale is None:
            refined = self._search(region, name, np.arange(max(.5, best[2] - .04), min(2, best[2] + .04) + .001, .005))
            if refined and refined[0] < best[0]:
                best = refined
        result = None
        threshold = .025 if name == "profile_check_preview" else self.threshold
        if best and best[0] <= threshold:
            score, (x, y), scale, w, h = best
            alternate = self.alternatives.get(name)
            accepted = True
            if alternate:
                alternative, _ = self._template(alternate, scale)
                # Paired references use exactly the same capture dimensions.
                other = float(cv2.matchTemplate(region[y:y+h, x:x+w], alternative, cv2.TM_SQDIFF_NORMED)[0, 0])
                accepted = np.isfinite(other) and score + .0002 < other
            if accepted:
                result = x + w // 2, offset + y + h // 2
                if name == "auto_profile" and self.scale is None:
                    self.scale = scale
        self.results[name] = result
        return result
