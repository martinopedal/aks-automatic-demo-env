"""Read-only GitHub protection check; never create an implicit environment."""
import json
import os
import subprocess


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def check_initiator(actor):
    if actor.lower() == "haflidif":
        raise ValueError("Haflidi cannot initiate and approve his own protected deployment. "
                         "Have Martin/another authorized non-Haflidi actor dispatch Deploy Demo VM "
                         "on main with action=upgrade for a fresh source-qualified run; "
                         "rerunning this run does not change its initiator. "
                         "No self-review fallback or VM change.")


def check(environment, policies, haflidi_id):
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(environment.get("can_admins_bypass") is False, "Admin bypass must be disabled")
    reviewers = [r for r in environment["protection_rules"] if r["type"] == "required_reviewers"]
    require(len(reviewers) == 1 and reviewers[0]["prevent_self_review"] is True, "Self-review forbidden")
    actual = reviewers[0]["reviewers"]
    require(len(actual) == 1 and actual[0]["type"] == "User" and actual[0]["reviewer"]["id"] == haflidi_id, "Haflidi-only gate required")
    require(environment["deployment_branch_policy"] == {
        "protected_branches": False, "custom_branch_policies": True
    }, "Exact main branch policy required")
    require(policies["total_count"] == 1 and policies["branch_policies"][0]["name"] == "main" and
            policies["branch_policies"][0]["type"] == "branch", "Only main, no tags")


if __name__ == "__main__":
    check_initiator(os.environ["GITHUB_ACTOR"])
    repo = os.environ["GITHUB_REPOSITORY"]
    name = "demo-vm-upgrade-haflidi"
    check(api(f"repos/{repo}/environments/{name}"),
          api(f"repos/{repo}/environments/{name}/deployment-branch-policies"),
          api("users/haflidif")["id"])
    print("Actual dedicated Haflidi-only main protection verified; existing online remains separate")
