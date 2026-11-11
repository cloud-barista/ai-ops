"""Merge the core API contracts into the platform's Swagger document."""

import argparse
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SOURCES = [
    ("llmop", "LLM_Op", "llmop/go/service-control-api/docs/swagger/swagger.json"),
    ("resource-ops", "Resource Ops", "resource_ops/docs/swagger/swagger.json"),
    ("deployment-agent", "Deployment Agent", "app_automation_agent/docs/openapi_deployment_agent.yaml"),
]


def to_openapi(document):
    if "openapi" in document:
        return document
    assert document["swagger"] == "2.0"
    for methods in document["paths"].values():
        for operation in methods.values():
            consumes = operation.pop("consumes", ["application/json"])
            produces = operation.pop("produces", ["application/json"])
            for parameter in operation.pop("parameters", []):
                if parameter["in"] in ("header", "path"):
                    # shortcut: these parameters are plain strings, move constraints into schema if the contract adds them.
                    assert parameter.pop("type") == "string", parameter
                    parameter["schema"] = {"type": "string"}
                    operation.setdefault("parameters", []).append(parameter)
                    continue
                assert parameter["in"] == "body", parameter
                operation["requestBody"] = {
                    "required": parameter.get("required", False),
                    "description": parameter.get("description", ""),
                    "content": {mime: {"schema": parameter["schema"]} for mime in consumes},
                }
            for response in operation["responses"].values():
                if "schema" in response:
                    schema = response.pop("schema")
                    response["content"] = {mime: {"schema": schema} for mime in produces}
    document["components"] = {"schemas": document.pop("definitions", {})}
    if "securityDefinitions" in document:
        document["components"]["securitySchemes"] = document.pop("securityDefinitions")
    return document


def namespace(value, service):
    if isinstance(value, list):
        return [namespace(child, service) for child in value]
    if not isinstance(value, dict):
        return value
    result = {key: namespace(child, service) for key, child in value.items()}
    if "$ref" in result:
        ref = result["$ref"].replace("#/definitions/", "#/components/schemas/")
        assert ref.startswith("#/components/"), ref
        section, name = ref.removeprefix("#/components/").split("/", 1)
        result["$ref"] = f"#/components/{section}/{service}.{name}"
    if "security" in result:
        result["security"] = [{f"{service}.{key}": scopes for key, scopes in requirement.items()} for requirement in result["security"]]
    if result.get("type") == "file":
        result.update(type="string", format="binary")
    return result


def generate():
    merged = {
        "openapi": "3.0.3",
        "info": {"title": "AI-Ops API", "version": "1.0.0", "description": "응용 요구사항 분석, 자원 추천, 배포 및 운영 판단 API."},
        "servers": [{"url": "/swagger/services"}],
        "tags": [],
        "paths": {},
        "components": {},
    }
    for service, title, filename in SOURCES:
        source = to_openapi(yaml.safe_load((ROOT / filename).read_text(encoding="utf-8")))
        source = namespace(source, service)
        merged["tags"].append({"name": title})
        for path, methods in source["paths"].items():
            for operation in methods.values():
                operation["tags"] = [title]
                operation["operationId"] = f'{service}.{operation["operationId"]}'
            merged["paths"][f"/{service}{path}"] = methods
        for section, entries in source["components"].items():
            merged["components"].setdefault(section, {}).update({f"{service}.{name}": schema for name, schema in entries.items()})
    (ROOT / "platform/data/openapi.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return merged


def build_pages(document, destination):
    destination.mkdir(parents=True, exist_ok=True)
    html = (ROOT / "platform/swagger.html").read_text(encoding="utf-8")
    marker = "validatorUrl: null"
    if html.count(marker) != 1:
        raise ValueError("Swagger UI template must contain one validatorUrl setting")
    html = html.replace(marker, marker + ",\n      supportedSubmitMethods: []")
    published = {
        **document,
        "servers": [{"url": "http://127.0.0.1:18100/swagger/services", "description": "별도로 실행한 로컬 API 게이트웨이"}],
    }
    (destination / "index.html").write_text(html, encoding="utf-8", newline="\n")
    (destination / "openapi.json").write_text(json.dumps(published, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", action="store_true", help="Build a read-only GitHub Pages site")
    args = parser.parse_args()
    document = generate()
    print(f'Merged {len(SOURCES)} services: {sum(len(methods) for methods in document["paths"].values())} operations')
    if args.pages:
        destination = ROOT / "output/swagger-pages"
        build_pages(document, destination)
        print(f"GitHub Pages artifact: {destination}")
