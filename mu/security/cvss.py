"""CVSS 3.1 base score. CVSS is owned by FIRST and used by permission.

Formula and rounding: https://www.first.org/cvss/v3.1/specification-document
Only the eight base metrics are accepted; temporal/environmental scores are not inferred.
"""
import math


def score_vector(vector: str) -> tuple[float, str]:
    parts = str(vector).split("/")
    if parts[0] != "CVSS:3.1":
        raise ValueError("a CVSS:3.1 base vector is required")
    metrics = {}
    allowed = {"AV": "NALP", "AC": "LH", "PR": "NLH", "UI": "NR",
               "S": "UC", "C": "NLH", "I": "NLH", "A": "NLH"}
    for part in parts[1:]:
        key, sep, val = part.partition(":")
        if not sep or key not in allowed or val not in list(allowed[key]) or key in metrics:
            raise ValueError("invalid or duplicate CVSS base metric")
        metrics[key] = val
    if set(metrics) != set(allowed):
        raise ValueError("all eight CVSS base metrics are required")
    m = metrics
    changed = m["S"] == "C"
    impact_values = {"N": 0, "L": .22, "H": .56}
    iss = 1 - math.prod(1 - impact_values[m[k]] for k in ("C", "I", "A"))
    impact = 7.52 * (iss - .029) - 3.25 * (iss - .02) ** 15 if changed else 6.42 * iss
    exploit = (8.22 * {"N": .85, "A": .62, "L": .55, "P": .2}[m["AV"]]
               * {"L": .77, "H": .44}[m["AC"]]
               * {"N": .85, "L": .68 if changed else .62, "H": .5 if changed else .27}[m["PR"]]
               * {"N": .85, "R": .62}[m["UI"]])
    raw = 0 if impact <= 0 else min((impact + exploit) * (1.08 if changed else 1), 10)
    # FIRST Appendix A's integer rounding avoids floating-point boundary errors.
    integer = round(raw * 100000)
    score = integer / 100000 if integer % 10000 == 0 else (integer // 10000 + 1) / 10
    level = "info" if score == 0 else "low" if score < 4 else "medium" if score < 7 else "high" if score < 9 else "critical"
    return score, level
