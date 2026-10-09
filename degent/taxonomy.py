"""Evidence-bound proposals and human-approved, immutable role/skill definitions."""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from .store import digest, read_json, read_jsonl, utc_now, write_json


def load_taxonomy(directory: Path) -> dict:
    path = directory / "taxonomy.json"
    return read_json(path) if path.exists() else {"version": None, "groups": {}, "assignments": {}, "skills": {}}


def propose(directory: Path, vector_data: dict | None = None, threshold: float = 0.85) -> dict:
    if not 0 <= threshold <= 1:
        raise ValueError("Discovery similarity must be between zero and one.")
    profiles = read_jsonl(directory / "role-profiles.jsonl")
    vectors, model = {}, "local-lexical-discovery-v1"
    if vector_data is not None:
        model = vector_data["model_signature"]
        if not isinstance(model, str) or not model:
            raise ValueError("Embedding model signature required.")
        for p in profiles:
            v = vector_data["profiles"][p["document_id"]]
            if v["profile_sha256"] != digest(p["text"]):
                raise ValueError("Embeddings must match the preserved duty/domain profile, not a skill matrix.")
            numbers = v["vector"]
            if not isinstance(numbers, list) or not numbers or any(type(x) not in {int, float} or not math.isfinite(x) for x in numbers):
                raise ValueError("Invalid embedding vector.")
            norm = math.sqrt(sum(x*x for x in numbers))
            if not norm:
                raise ValueError("Zero embedding vector.")
            vectors[p["document_id"]] = {str(i): x/norm for i, x in enumerate(numbers)}
        if len({len(v) for v in vectors.values()}) > 1:
            raise ValueError("Embedding dimensions differ.")
    else:
        counts = {p["document_id"]: Counter(re.findall(r"[a-z]{3,}", p["text"].lower())) for p in profiles}
        frequency = Counter(t for c in counts.values() for t in c)
        for identity, c in counts.items():
            weighted = {t: (1+math.log(n))*math.log((len(counts)+1)/(frequency[t]+1)) for t,n in c.items()
                        if t not in {"senior", "associate", "junior", "principal", "manager", "director", "capital", "one"}}
            norm = math.sqrt(sum(x*x for x in weighted.values())) or 1
            vectors[identity] = {t:x/norm for t,x in weighted.items() if x}
    # An inverted index avoids dense all-pairs work for local lexical discovery.
    index = defaultdict(set)
    for identity, vec in vectors.items():
        for term in vec:
            index[term].add(identity)
    neighbors = []
    for p in profiles:
        vec = vectors[p["document_id"]]
        candidates = set().union(*(index[t] for t in vec)) if vec else set()
        scores = []
        for other in candidates - {p["document_id"]}:
            score = sum(x*vectors[other].get(t, 0) for t,x in vec.items())
            scores.append((score, other))
        neighbors.append({"document_id": p["document_id"], "source_sha256": p["source_sha256"],
                          "neighbors": [{"document_id": identity, "similarity": round(score, 5)}
                                        for score, identity in sorted(scores, reverse=True)[:5]]})
    adjacency = defaultdict(set)
    nearest = {r["document_id"]: {n["document_id"] for n in r["neighbors"] if n["similarity"] >= threshold} for r in neighbors}
    for identity, matches in nearest.items():
        for other in matches:
            if identity in nearest.get(other, set()):
                adjacency[identity].add(other)
                adjacency[other].add(identity)
    clusters, visited = [], set()
    for profile in profiles:
        identity = profile["document_id"]
        if identity in visited:
            continue
        members, queue = set(), [identity]
        while queue:
            member = queue.pop()
            if member not in members:
                members.add(member)
                queue.extend(adjacency[member] - members)
        visited |= members
        clusters.append({"proposal_id": "p-"+digest(json.dumps(sorted(members)))[:12],
                         "members": sorted(members), "approval": "pending", "definition": None,
                         "titles": sorted({p["title"] for p in profiles if p["document_id"] in members})})
    unresolved = defaultdict(list)
    for record in read_jsonl(directory / "requirements.jsonl"):
        if record.get("accepted"):
            for r in record["requirements"]:
                if not r.get("concept"):
                    unresolved[r.get("original_concept", r["phrase"])].append({"document_id": record["document_id"], "evidence": r["evidence"], "category": r["category"]})
                for alternative in r.get("alternative_concepts", []):
                    if not alternative.get("concept"):
                        unresolved[alternative["phrase"]].append({"document_id": record["document_id"], "evidence": r["evidence"], "category": "other", "alternative_logic": r.get("alternative_logic")})
    result = {"status": "proposals_need_human_review", "method": model, "discovery_threshold": threshold,
              "rules": "similarity is not functional equivalence; review duties/domain, keep levels separate",
              "neighbors": neighbors, "clusters": clusters, "unresolved_skills": dict(unresolved),
              "groups": {}, "assignments": {}, "skills": {}}
    write_json(directory / "role-proposals.json", result)
    return result


def approve(directory: Path, payload: dict) -> dict:
    old = load_taxonomy(directory)
    observations = {r["document_id"]: r for r in read_jsonl(directory / "observations.jsonl")}
    if not payload.get("reviewer") or payload.get("approved") is not True:
        raise ValueError("Explicit human approval and reviewer are required.")
    groups, skills, assignments = payload.get("groups", {}), payload.get("skills", {}), payload.get("assignments", {})
    if not all(isinstance(x, dict) for x in (groups, skills, assignments)):
        raise ValueError("Definitions and assignments must be objects.")
    for kind, definitions in (("groups", groups), ("skills", skills)):
        for identity, row in definitions.items():
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", identity) or not isinstance(row, dict) or not row.get("name") or not row.get("definition"):
                raise ValueError("Definitions need stable IDs, names and explicit boundaries.")
            if kind == "groups" and (not row.get("function") or not row.get("domains")):
                raise ValueError("Role groups need function and domains.")
            if kind == "skills" and row.get("category") not in {"technical", "tool", "domain", "people_skills", "work_habits", "ai_use", "ai_engineering", "experience", "education", "certification", "other"}:
                raise ValueError("Invalid skill category.")
            previous = old[kind].get(identity)
            definition = {k:v for k,v in row.items() if k != "approved"}
            if previous and definition != {k:v for k,v in previous.items() if k != "approved"}:
                raise ValueError("Frozen definition changed: create a new ID and record supersedes lineage.")
            row["approved"] = True
            for parent in row.get("supersedes", []):
                if parent not in old[kind]:
                    raise ValueError("Unknown split/merge parent.")
    for doc, assignment in assignments.items():
        obs = observations.get(doc)
        if not obs or assignment.get("source_sha256") != obs["clean_sha256"]:
            raise ValueError("Assignment does not match preserved evidence.")
        excluded = assignment.get("scope_decision") == "excluded"
        if not excluded and assignment.get("role_group_id") not in groups:
            raise ValueError("Assignment requires an approved group.")
        evidence = assignment.get("evidence")
        source = Path(obs["clean_file"]).read_text()
        if digest(source) != obs["clean_sha256"]:
            raise ValueError("Assignment source changed; preserve and reassess a new version.")
        if not isinstance(evidence, str) or not evidence or evidence not in source:
            raise ValueError("Assignment requires exact duty/domain evidence.")
        if assignment.get("scope_decision", "included") not in {"included", "excluded"}:
            raise ValueError("Invalid scope decision.")
        if excluded and not assignment.get("exclusion_reason"):
            raise ValueError("Scope exclusion requires a reason.")
        if not excluded:
            from .evidence import role_profile, select_units
            profile = role_profile(select_units(source), obs["title"])
            if evidence not in profile["text"]:
                raise ValueError("Role assignment needs duty/domain/title evidence, independently of selected qualification skills.")
        if "level" in assignment:
            if assignment["level"] not in {"entry", "mid", "senior", "management", "executive", "internship", "unknown"}:
                raise ValueError("Invalid reviewed seniority stratum.")
            if not assignment.get("seniority_evidence") or assignment["seniority_evidence"] not in source:
                raise ValueError("Reviewed seniority needs exact source evidence.")
        if "employment_type" in assignment:
            if assignment["employment_type"] not in {"full-time", "part-time", "contract", "temporary", "internship", "per-diem", "volunteer", "unknown"}:
                raise ValueError("Invalid reviewed employment stratum.")
            if not assignment.get("employment_type_evidence") or assignment["employment_type_evidence"] not in source:
                raise ValueError("Reviewed employment type needs exact source evidence.")
    for kind, definitions in (("groups", groups), ("skills", skills)):
        for identity, definition in definitions.items():
            evidence = definition.get("supporting_evidence", [])
            if not isinstance(evidence, list):
                raise ValueError("Supporting evidence must be a list.")
            for ref in evidence:
                obs = observations.get(ref.get("document_id"))
                if not obs or ref.get("source_sha256") != obs["clean_sha256"]:
                    raise ValueError("Definition evidence source mismatch.")
                source = Path(obs["clean_file"]).read_text()
                if digest(source) != obs["clean_sha256"] or not ref.get("quote") or ref["quote"] not in source:
                    raise ValueError("Definition needs exact preserved supporting evidence.")
            supported_by_assignment = kind == "groups" and any(a.get("role_group_id") == identity for a in assignments.values())
            if not evidence and not supported_by_assignment and identity not in old[kind]:
                raise ValueError("New definitions require supporting source evidence.")
    for kind in ("groups", "skills"):
        for removed in set(old[kind]) - set(payload.get(kind, {})):
            if not any(removed in r.get("supersedes", []) for r in payload.get(kind, {}).values()):
                raise ValueError("Removing a frozen definition requires split/merge lineage.")
    # Prevent aliases making two canonical concepts interchangeable accidentally.
    aliases = {}
    from .vocabulary import vocabulary
    existing_skills = vocabulary()
    for identity, row in existing_skills.items():
        for phrase in row["phrases"]:
            aliases[phrase.casefold()] = identity
    for identity, row in skills.items():
        for phrase in [row["name"], *row.get("aliases", [])]:
            key = phrase.casefold()
            if key in aliases and aliases[key] != identity:
                raise ValueError("Ambiguous approved skill alias.")
            if identity in existing_skills and row["category"] != existing_skills[identity]["category"]:
                raise ValueError("Existing concept category is frozen; create a new concept with lineage.")
            aliases[key] = identity
    content = {"groups": groups, "skills": skills, "assignments": assignments}
    version = "taxonomy-" + digest(json.dumps(content, sort_keys=True))[:16]
    result = {**content, "version": version, "reviewer": payload["reviewer"], "approved_at": utc_now()}
    path = directory / "taxonomies" / (version + ".json")
    if not path.exists():
        write_json(path, result)
    write_json(directory / "taxonomy.json", result)
    return result
