from pathlib import Path
import yaml
from kubernetes_validate import validate
import pytest


@pytest.mark.parametrize("path", sorted(Path("k8s").rglob("*.yaml")))
def test_kubernetes_manifest_matches_schema(path):
    document = yaml.safe_load(path.read_text())
    validate(document, desired_version="1.31.0", strict=True)
    if document["kind"] != "Namespace":
        assert document["metadata"]["namespace"] == "crewai"
    if document["kind"] == "Deployment":
        spec = document["spec"]["template"]["spec"]
        assert isinstance(spec["containers"], list)
        assert all(
            isinstance(v, str) for v in document["spec"]["template"]["metadata"]["labels"].values()
        )


@pytest.mark.parametrize(
    "path,tenant",
    [
        ("k8s/base/worker/deployment.yaml", "1"),
        ("k8s/workers/worker-deployment.yaml", "default"),
        ("k8s/tenants/tenant-a.yaml", "tenant_a"),
        ("k8s/tenants/tenant-b.yaml", "tenant_b"),
    ],
)
def test_deployment_worker_tenant_is_explicit(path, tenant):
    container = yaml.safe_load(Path(path).read_text())["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["worker"]
    assert {"name": "TENANT_ID", "value": tenant} in container["env"]
