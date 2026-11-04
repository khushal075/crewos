"""Scripted reviewer outputs test plumbing, not semantic model accuracy."""


def abstention_review():
    return {
        "verdict": "appropriate_abstention",
        "evidence_answerability": "insufficient",
        "reason": "Evidence is incomplete",
        "covers_all_claims": False,
        "addresses_question": True,
        "evidence_conflict": False,
        "claims": [],
    }


def support_review(source, quote="Use backoff"):
    return {
        "verdict": "supported",
        "evidence_answerability": "answerable",
        "reason": "Claim matches the passage",
        "covers_all_claims": True,
        "addresses_question": True,
        "evidence_conflict": False,
        "claims": [
            {
                "claim": "Use backoff.",
                "verdict": "supported",
                "excerpts": [{"source": source, "quote": quote}],
            }
        ],
    }
