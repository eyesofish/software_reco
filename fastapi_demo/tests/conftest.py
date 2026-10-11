"""Opt-in isolation for the local/CI offline entry point."""

import os

if os.environ.get("SOFTWARE_RECO_OFFLINE_TESTS") == "1":
    from evaluation.offline_support import block_external_network

    block_external_network()
