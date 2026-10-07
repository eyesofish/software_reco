"""Require a dedicated, explicitly named keyword index for retrieval evaluation."""

import re


def validate_keyword_index(name: str) -> str:
    if len(name.encode("utf-8")) > 255 or not re.fullmatch(r"software_reco_eval_[a-z0-9_-]+", name):
        raise ValueError(
            "Evaluation index must start with software_reco_eval_ and contain only lowercase a-z, 0-9, _ or -"
        )
    return name
