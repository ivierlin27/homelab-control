#!/usr/bin/env python3
"""Shared project-agent registry, intake matching, and routing helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any


DEFAULT_TASK_CLASS = "summarize"
DEFAULT_SYMBOLIC_INTENT = "summarize"
DEFAULT_ROUTE = "local-fast"


def normalize_slug(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def project_registry(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    projects = policy.get("project_agents", {})
    if not isinstance(projects, dict):
        return {}
    return {normalize_slug(name): dict(config or {}) for name, config in projects.items()}


def project_for_domain(policy: dict[str, Any], domain: str) -> str:
    normalized = normalize_slug(domain)
    for project_name, config in project_registry(policy).items():
        if normalize_slug(str(config.get("domain", project_name))) == normalized:
            return project_name
    return normalized


def task_class_registry(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    classes = policy.get("task_classes", {})
    if not isinstance(classes, dict):
        return {}
    return {normalize_slug(name): dict(config or {}) for name, config in classes.items()}


def symbolic_route_registry(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    routes = policy.get("symbolic_routes", {})
    if not isinstance(routes, dict):
        return {}
    return {normalize_slug(name): dict(config or {}) for name, config in routes.items()}


def classify_task_class(policy: dict[str, Any], text: str, hint: str = "") -> dict[str, Any]:
    haystack = " ".join(part for part in [text, hint] if part).lower()
    classes = task_class_registry(policy)
    best_name = DEFAULT_TASK_CLASS
    best_score = -1
    best_reason = "fell back to default task class"
    best_intent = DEFAULT_SYMBOLIC_INTENT

    for name, config in classes.items():
        score = 0
        keywords = [str(item).lower() for item in config.get("keywords", [])]
        for keyword in keywords:
            if keyword and keyword in haystack:
                score += 1
        if config.get("prefix") and haystack.startswith(str(config["prefix"]).lower()):
            score += 2
        if score > best_score:
            best_name = name
            best_score = score
            best_reason = f"matched {score} keyword(s)" if score > 0 else "no keyword match"
            best_intent = normalize_slug(str(config.get("symbolic_intent", DEFAULT_SYMBOLIC_INTENT)))

    return {
        "task_class": best_name,
        "symbolic_intent": best_intent,
        "reason": best_reason,
        "used_heuristics": True,
    }


def match_project_for_intake(policy: dict[str, Any], text: str, hint: str = "") -> dict[str, Any]:
    haystack = " ".join(part for part in [text, hint] if part).lower()
    projects = project_registry(policy)
    scored: list[dict[str, Any]] = []
    for project_name, config in projects.items():
        score = 0
        matches: list[str] = []
        tokens = [project_name, str(config.get("domain", ""))] + [str(item) for item in config.get("intake_match_hints", [])]
        for token in tokens:
            cleaned = token.strip().lower()
            if cleaned and cleaned in haystack:
                score += 1
                matches.append(cleaned)
        if score:
            scored.append(
                {
                    "project": project_name,
                    "score": score,
                    "matches": sorted(set(matches)),
                    "domain": config.get("domain", project_name),
                    "queue_dir_name": config.get("queue_dir_name", ""),
                }
            )

    scored.sort(key=lambda item: (-item["score"], item["project"]))
    best = scored[0] if scored else None
    if best:
        return {
            "classification": "existing_project",
            "candidate": best,
            "alternates": scored[1:3],
        }

    exploratory_terms = ["idea", "project", "build", "start", "new", "workflow", "agent", "system", "automation"]
    if any(term in haystack for term in exploratory_terms):
        return {
            "classification": "new_project_candidate",
            "candidate": None,
            "alternates": [],
        }

    return {
        "classification": "scratch",
        "candidate": None,
        "alternates": [],
    }


def resolve_route(
    policy: dict[str, Any],
    *,
    project: str,
    task_class: str,
    symbolic_intent: str = DEFAULT_SYMBOLIC_INTENT,
) -> dict[str, Any]:
    project_name = normalize_slug(project)
    task_class_name = normalize_slug(task_class or DEFAULT_TASK_CLASS)
    intent_name = normalize_slug(symbolic_intent or DEFAULT_SYMBOLIC_INTENT)
    projects = project_registry(policy)
    project_config = projects.get(project_name, {})
    routing = project_config.get("routing_policy", {})
    routes = symbolic_route_registry(policy)
    route_defaults = routes.get(intent_name, routes.get(DEFAULT_SYMBOLIC_INTENT, {}))
    default_route = normalize_slug(str(route_defaults.get("default_route", DEFAULT_ROUTE)))
    route = normalize_slug(str(routing.get("route_overrides", {}).get(task_class_name, default_route)))
    reason = "default symbolic route"
    cloud_allowed = False

    if task_class_name in [normalize_slug(item) for item in routing.get("local_only", [])]:
        reason = "project policy local_only"
        cloud_allowed = False
        if route.startswith("cloud-"):
            route = DEFAULT_ROUTE
    elif task_class_name in [normalize_slug(item) for item in routing.get("local_preferred", [])]:
        reason = "project policy local_preferred"
        cloud_allowed = False
    elif task_class_name in [normalize_slug(item) for item in routing.get("cloud_allowed", [])]:
        reason = "project policy cloud_allowed"
        cloud_allowed = True
        if not route:
            route = "cloud-frontier"
    elif project_config:
        reason = "project policy fallback"
        cloud_allowed = bool(routing.get("default_cloud_allowed", False))

    if not route:
        route = DEFAULT_ROUTE

    route_config = routes.get(route, route_defaults)
    return {
        "project": project_name,
        "task_class": task_class_name,
        "symbolic_intent": intent_name,
        "route": route,
        "provider": route_config.get("provider", route),
        "model_tier": route_config.get("model_tier", route),
        "cloud_allowed": cloud_allowed,
        "requires_review": bool(route.startswith("cloud-") and routing.get("cloud_required_review", False)),
        "reason": reason,
    }


def queue_dir_for_project(policy: dict[str, Any], state_root: Path, project: str) -> Path:
    project_config = project_registry(policy).get(normalize_slug(project), {})
    queue_dir_name = str(project_config.get("queue_dir_name", "")).strip()
    if queue_dir_name:
        return state_root / queue_dir_name
    return state_root / f"agent-{normalize_slug(project)}"
