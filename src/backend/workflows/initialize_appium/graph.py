from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


JsonObject = dict[str, Any]

ACTION_PATTERN = re.compile(
    r'- \[(element_\d+)\] ([a-z_]+) \| "([^"]*)"',
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExplorationGraphStore:
    """Persistent screen graph and evolving agent knowledge."""

    def __init__(
        self,
        *,
        package_id: str,
        objective: str,
        output_directory: Path | None = None,
    ) -> None:
        self.output_directory = output_directory
        now = utc_now()
        self.graph: JsonObject = {
            "contract": "workflow.exploration_graph",
            "schema_version": 1,
            "package_id": package_id,
            "objective": objective,
            "created_at": now,
            "updated_at": now,
            "status": "running",
            "current_node_id": None,
            "nodes": [],
            "edges": [],
            "iterations": 0,
        }
        self.knowledge: JsonObject = {
            "contract": "workflow.app_knowledge_box",
            "schema_version": 1,
            "package_id": package_id,
            "app_summary": "",
            "features": [],
            "facts": [],
            "hypotheses": [],
            "workflows": [],
            "failures": [],
            "open_questions": [],
            "notes": [],
            "updated_at": now,
        }
        self.persist()

    def observe_screen(
        self,
        *,
        screen_id: str,
        fingerprint: str | None,
        context_text: str,
        package: str | None = None,
        activity: str | None = None,
    ) -> JsonObject:
        identity = fingerprint or screen_id
        node = self._node_by_identity(identity)
        now = utc_now()
        if node is None:
            node = {
                "node_id": f"node_{len(self.graph['nodes']) + 1}",
                "identity": identity,
                "fingerprint": fingerprint,
                "screen_ids": [screen_id],
                "package": package,
                "activity": activity,
                "summary": "",
                "purpose": "",
                "observations": [],
                "controls": [],
                "actions": [],
                "visit_count": 1,
                "first_seen_at": now,
                "last_seen_at": now,
            }
            self.graph["nodes"].append(node)
        else:
            node["visit_count"] = int(node.get("visit_count", 0)) + 1
            node["last_seen_at"] = now
            if screen_id not in node["screen_ids"]:
                node["screen_ids"].append(screen_id)
            node["package"] = package or node.get("package")
            node["activity"] = activity or node.get("activity")
        self._merge_available_actions(node, context_text)
        self.graph["current_node_id"] = node["node_id"]
        self.graph["updated_at"] = now
        self.persist()
        return node

    def apply_agent_understanding(
        self,
        node_id: str,
        decision: JsonObject,
    ) -> None:
        node = self.node(node_id)
        node["summary"] = decision.get("screen_summary") or node.get("summary")
        node["purpose"] = decision.get("screen_purpose") or node.get("purpose")
        self._extend_unique(node["observations"], decision.get("observations", []))
        self._merge_controls(node, decision.get("controls", []))
        for blocked in decision.get("blocked_actions", []):
            if not isinstance(blocked, dict):
                continue
            action = self._action(node, str(blocked.get("action_key") or ""))
            if action is not None:
                action["status"] = "blocked"
                action["blocked_reason"] = str(blocked.get("reason") or "")
        update = decision.get("knowledge_update", {})
        if isinstance(update, dict):
            summary = update.get("app_summary")
            if isinstance(summary, str) and summary.strip():
                self.knowledge["app_summary"] = summary.strip()
            for field in (
                "features",
                "failures",
                "open_questions",
                "notes",
            ):
                self._extend_unique(
                    self.knowledge[field],
                    update.get(field, []),
                )
            for fact in update.get("facts", []):
                if not isinstance(fact, str):
                    continue
                if re.search(
                    r"\b(should|likely|may|might|expected|probably|perhaps)\b",
                    fact,
                    re.I,
                ):
                    self._extend_unique(self.knowledge["hypotheses"], [fact])
                else:
                    self._extend_unique(self.knowledge["facts"], [fact])
            self._extend_unique(
                self.knowledge["hypotheses"],
                update.get("hypotheses", []),
            )
            self._merge_workflows(update.get("workflows", []))
        self.knowledge["updated_at"] = utc_now()
        self.graph["updated_at"] = utc_now()
        self.persist()

    def record_transition(
        self,
        *,
        iteration: int,
        from_node_id: str,
        action_request: JsonObject,
        action_result: JsonObject,
    ) -> JsonObject:
        after = action_result.get("after", {})
        health = action_result.get("app_health", {})
        target = self.observe_screen(
            screen_id=str(after.get("screen_id") or f"unknown_{iteration}"),
            fingerprint=(
                str(after["fingerprint"])
                if after.get("fingerprint")
                else None
            ),
            context_text="",
            package=(
                str(health["current_package"])
                if health.get("current_package")
                else None
            ),
        )
        source = self.node(from_node_id)
        action_key = self.action_key(action_request)
        source_action = self._action(source, action_key)
        if source_action is None:
            source_action = {
                "action_key": action_key,
                "action": action_request.get("action"),
                "element_id": action_request.get("target", {}).get("element_id"),
                "label": "",
                "status": "explored",
                "attempts": 0,
                "outcomes": [],
            }
            source["actions"].append(source_action)
        source_action["status"] = "explored"
        source_action["attempts"] = int(source_action.get("attempts", 0)) + 1
        outcome = {
            "classification": action_result.get("classification"),
            "app_health": health.get("status"),
            "to_node_id": target["node_id"],
        }
        source_action.setdefault("outcomes", []).append(outcome)
        edge = {
            "edge_id": f"edge_{len(self.graph['edges']) + 1}",
            "iteration": iteration,
            "from_node_id": from_node_id,
            "to_node_id": target["node_id"],
            "action_key": action_key,
            "action": action_request,
            "classification": action_result.get("classification"),
            "app_health": health,
            "effect": action_result.get("effect"),
            "errors": action_result.get("errors", []),
            "transition_id": action_result.get("transition_id"),
            "created_at": utc_now(),
        }
        self.graph["edges"].append(edge)
        self.graph["current_node_id"] = target["node_id"]
        self.graph["iterations"] = iteration
        unhealthy = str(health.get("status") or "")
        if unhealthy in {"crashed", "anr", "process_terminated"}:
            self._extend_unique(
                self.knowledge["failures"],
                [
                    (
                        f"{action_key} from {source['node_id']} caused "
                        f"{unhealthy}."
                    )
                ],
            )
        self.graph["updated_at"] = utc_now()
        self.knowledge["updated_at"] = utc_now()
        self.persist()
        return edge

    def finish(self, status: str) -> None:
        self.graph["status"] = status
        self.graph["updated_at"] = utc_now()
        self.persist()

    def node(self, node_id: str) -> JsonObject:
        for node in self.graph["nodes"]:
            if node.get("node_id") == node_id:
                return node
        raise KeyError(f"Unknown graph node '{node_id}'.")

    def action_key(self, action_request: JsonObject) -> str:
        element_id = action_request.get("target", {}).get("element_id")
        return (
            f"{action_request.get('action')}:{element_id}"
            if element_id
            else str(action_request.get("action"))
        )

    def unresolved_actions(
        self,
        node_id: str,
        proposed_blocked: list[JsonObject] | None = None,
    ) -> list[JsonObject]:
        blocked = {
            str(item.get("action_key"))
            for item in proposed_blocked or []
            if isinstance(item, dict)
        }
        return [
            action
            for action in self.node(node_id)["actions"]
            if action.get("status") == "unexplored"
            and action.get("action_key") not in blocked
        ]

    def total_unresolved_actions(
        self,
        current_node_id: str,
        proposed_blocked: list[JsonObject] | None = None,
    ) -> list[JsonObject]:
        unresolved: list[JsonObject] = []
        for node in self.graph["nodes"]:
            actions = (
                self.unresolved_actions(node["node_id"], proposed_blocked)
                if node["node_id"] == current_node_id
                else [
                    action
                    for action in node.get("actions", [])
                    if action.get("status") == "unexplored"
                ]
            )
            unresolved.extend(
                {
                    **action,
                    "node_id": node["node_id"],
                }
                for action in actions
            )
        return unresolved

    def known_action(self, node_id: str, action_request: JsonObject) -> bool:
        return self._action(
            self.node(node_id),
            self.action_key(action_request),
        ) is not None

    def is_explored(self, node_id: str, action_request: JsonObject) -> bool:
        action = self._action(
            self.node(node_id),
            self.action_key(action_request),
        )
        return bool(action and action.get("status") == "explored")

    def agent_context(self, current_node_id: str) -> JsonObject:
        nodes = []
        for node in self.graph["nodes"]:
            nodes.append(
                {
                    "node_id": node["node_id"],
                    "current": node["node_id"] == current_node_id,
                    "package": node.get("package"),
                    "activity": node.get("activity"),
                    "summary": node.get("summary"),
                    "purpose": node.get("purpose"),
                    "visit_count": node.get("visit_count"),
                    "actions": [
                        {
                            "action_key": action.get("action_key"),
                            "label": action.get("label"),
                            "status": action.get("status"),
                            "attempts": action.get("attempts"),
                            "blocked_reason": action.get("blocked_reason"),
                            "last_outcome": (
                                action.get("outcomes", [])[-1]
                                if action.get("outcomes")
                                else None
                            ),
                        }
                        for action in node.get("actions", [])
                    ],
                }
            )
        return {
            "current_node_id": current_node_id,
            "nodes": nodes,
            "recent_edges": [
                {
                    "edge_id": edge.get("edge_id"),
                    "from_node_id": edge.get("from_node_id"),
                    "to_node_id": edge.get("to_node_id"),
                    "action_key": edge.get("action_key"),
                    "classification": edge.get("classification"),
                    "app_health": edge.get("app_health", {}).get("status"),
                }
                for edge in self.graph["edges"][-30:]
            ],
            "coverage": self.coverage(),
        }

    def coverage(self) -> JsonObject:
        actions = [
            action
            for node in self.graph["nodes"]
            for action in node.get("actions", [])
        ]
        explored = sum(action.get("status") == "explored" for action in actions)
        blocked = sum(action.get("status") == "blocked" for action in actions)
        return {
            "screens_discovered": len(self.graph["nodes"]),
            "transitions_recorded": len(self.graph["edges"]),
            "actions_discovered": len(actions),
            "actions_explored": explored,
            "actions_blocked": blocked,
            "actions_remaining": max(0, len(actions) - explored - blocked),
        }

    def viewer_snapshot(self) -> JsonObject:
        return {
            "graph": {
                "status": self.graph["status"],
                "current_node_id": self.graph["current_node_id"],
                "coverage": self.coverage(),
                "nodes": self.graph["nodes"],
                "edges": self.graph["edges"],
            },
            "knowledge_box": self.knowledge,
        }

    def persist(self) -> None:
        if self.output_directory is None:
            return
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self._write(
            self.output_directory / "exploration-graph.json",
            self.graph,
        )
        self._write(
            self.output_directory / "knowledge-box.json",
            self.knowledge,
        )

    def _node_by_identity(self, identity: str) -> JsonObject | None:
        return next(
            (
                node
                for node in self.graph["nodes"]
                if node.get("identity") == identity
            ),
            None,
        )

    @staticmethod
    def _action(node: JsonObject, action_key: str) -> JsonObject | None:
        return next(
            (
                action
                for action in node.get("actions", [])
                if action.get("action_key") == action_key
            ),
            None,
        )

    def _merge_available_actions(
        self,
        node: JsonObject,
        context_text: str,
    ) -> None:
        for element_id, action_name, label in ACTION_PATTERN.findall(context_text):
            action_key = f"{action_name}:{element_id}"
            if self._action(node, action_key) is None:
                node["actions"].append(
                    {
                        "action_key": action_key,
                        "action": action_name,
                        "element_id": element_id,
                        "label": label,
                        "status": "unexplored",
                        "attempts": 0,
                        "outcomes": [],
                    }
                )

    @staticmethod
    def _merge_controls(node: JsonObject, controls: Any) -> None:
        if not isinstance(controls, list):
            return
        known = {
            str(control.get("element_id"))
            for control in node["controls"]
            if isinstance(control, dict)
        }
        for control in controls:
            if not isinstance(control, dict):
                continue
            element_id = str(control.get("element_id") or "")
            if element_id and element_id not in known:
                node["controls"].append(control)
                known.add(element_id)

    def _merge_workflows(self, workflows: Any) -> None:
        if not isinstance(workflows, list):
            return
        known = {
            json.dumps(item, sort_keys=True, ensure_ascii=False)
            for item in self.knowledge["workflows"]
        }
        for workflow in workflows:
            if not isinstance(workflow, dict):
                continue
            key = json.dumps(workflow, sort_keys=True, ensure_ascii=False)
            if key not in known:
                self.knowledge["workflows"].append(workflow)
                known.add(key)

    @staticmethod
    def _extend_unique(target: list[Any], values: Any) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            if value not in target:
                target.append(value)

    @staticmethod
    def _write(path: Path, value: JsonObject) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(path)
