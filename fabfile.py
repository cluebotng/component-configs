import base64
import dataclasses
import os
import sys
import time
import yaml
from pathlib import PosixPath
from typing import List, Optional, Dict, Any

import requests
from fabric import Connection, Config, task


TARGET_USER = os.environ.get("TARGET_USER")
TOOL_BASE_DIR = PosixPath("/data/project")


@dataclasses.dataclass
class WebServiceConfig:
    tool_name: str
    target_component: str
    target_port: int

    def as_k8s_object(self) -> Dict[str, Any]:
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": f"{self.tool_name}-subdomain",
                "labels": {
                    "name": self.tool_name,
                    "toolforge": "tool",
                }
            },
            "spec": {
                "ingressClassName": "toolforge",
                "rules": [
                    {
                        "host": f"{self.tool_name}.toolforge.org",
                        "http": {
                            "paths": [
                                {
                                    "path": "/",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": self.target_component,
                                            "port": {"number": self.target_port},
                                        }
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
        }

    def __str__(self) -> str:
        return f"Ingress(backend={self.target_component}, port={self.target_port})"

    @staticmethod
    def from_values(tool_name: str, data: Dict[str, Any]) -> "WebServiceConfig":
        return WebServiceConfig(
            tool_name=tool_name,
            target_component=data.get("component"),
            target_port=data.get("port"),
        )


@dataclasses.dataclass
class NetworkPolicyPodIngress:
    name: str

    def as_k8s_object(self) -> Dict[str, Any]:
        return {"podSelector": {"matchLabels": {"app.kubernetes.io/name": self.name}}}

    @staticmethod
    def from_values(data: Dict[str, Any]) -> "NetworkPolicyPodIngress":
        return NetworkPolicyPodIngress(name=data.get("pod"))


@dataclasses.dataclass
class NetworkPolicyNamespaceIngress:
    name: str

    def as_k8s_object(self) -> Dict[str, Any]:
        return {"namespaceSelector": {"matchLabels": {"name": self.name}}}

    @staticmethod
    def from_values(data: Dict[str, Any]) -> "NetworkPolicyNamespaceIngress":
        return NetworkPolicyNamespaceIngress(name=data.get("namespace"))


@dataclasses.dataclass
class NetworkPolicy:
    name: str
    match: str
    allow: List[NetworkPolicyPodIngress | NetworkPolicyNamespaceIngress]
    delete: bool

    @property
    def k8s_type(self) -> "str":
        return "NetworkPolicy"

    def __str__(self) -> str:
        return f"NetworkPolicy(name={self.name})"

    def as_k8s_object(self) -> Dict[str, Any]:
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": self.name,
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {
                        "app.kubernetes.io/name": self.match,
                    },
                },
                "ingress": [{"from": [entry.as_k8s_object() for entry in self.allow]}],
            },
        }

    @staticmethod
    def from_values(data: Dict[str, Any]) -> "NetworkPolicy":
        should_delete = data.get("delete", False)

        allow_from = []
        if not should_delete:
            for entry in data.get("allow", []):
                if "namespace" in entry:
                    allow_from.append(NetworkPolicyNamespaceIngress.from_values(entry))
                elif "pod" in entry:
                    allow_from.append(NetworkPolicyPodIngress.from_values(entry))

        return NetworkPolicy(
            name=data.get("name"),
            match=data.get("match"),
            allow=allow_from,
            delete=should_delete,
        )


def _get_head_ref() -> Optional[str]:
    r = requests.get(
        "https://api.github.com/repos/cluebotng/component-configs/git/refs"
    )
    r.raise_for_status()
    for branch in r.json():
        if branch["ref"] == "refs/heads/main":
            return branch["object"]["sha"]
    return None


def _get_connection_for_tool(tool_name: str) -> Connection:
    return Connection(
        "login.toolforge.org",
        config=Config(
            overrides={
                "sudo": {"user": f"tools.{tool_name}", "prefix": "/usr/bin/sudo -ni"}
            }
        ),
    )


def _get_config_url(config_url: str, latest_sha: Optional[str]):
    if not latest_sha:
        latest_sha = "refs/heads/main"
    return f"https://raw.githubusercontent.com/cluebotng/component-configs/{latest_sha}/{config_url}.yaml"


def _get_target_tools() -> List[str]:
    return [
        config.name.split(".yaml")[0]
        for config in PosixPath(__file__).parent.glob("*.yaml")
    ]


def _get_web_services() -> Dict[str, WebServiceConfig]:
    config = {}
    config_path = PosixPath(__file__).parent / "config" / "web-services"
    if config_path.is_dir():
        for path in config_path.glob("*.yaml"):
            with path.open("r") as fh:
                config.update(
                    {
                        path.name.split(".yaml")[0]: yaml.load(
                            fh.read(), Loader=yaml.SafeLoader
                        )
                    }
                )

    return {
        tool_name: WebServiceConfig.from_values(tool_name, config)
        for tool_name, config in config.items()
    }


def _get_network_policies() -> Dict[str, WebServiceConfig]:
    config = {}
    config_path = PosixPath(__file__).parent / "config" / "network-policies"
    if config_path.is_dir():
        for path in config_path.glob("*.yaml"):
            with path.open("r") as fh:
                config.update(
                    {
                        path.name.split(".yaml")[0]: yaml.load(
                            fh.read(), Loader=yaml.SafeLoader
                        )
                    }
                )

    return {
        tool_name: [NetworkPolicy.from_values(config) for config in entries]
        for tool_name, entries in config.items()
    }


def _setup_component_configs(c: Connection, tool_name: str):
    with (PosixPath(__name__).parent / f"{tool_name}.yaml").open("r") as fh:
        config = fh.read()

    encoded_config = base64.b64encode(config.encode("utf-8")).decode("utf-8")

    c.sudo(
        f"bash -c 'base64 -d <<<{encoded_config} | "
        f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / tool_name}' toolforge components config create'",
    )


def _get_deployment_token(c: Connection, tool_name: str):
    return c.sudo(
        f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / tool_name}' toolforge components deploy-token show --json | jq -r .token",
        hide="stdout",
    ).stdout.strip()


def _apply_kubernetes_object(c: Connection, k8s_obj: Dict[str, Any]) -> bool:
    obj_as_yaml = yaml.dump(k8s_obj)
    encoded_contents = base64.b64encode(obj_as_yaml.encode("utf-8")).decode("utf-8")
    ret = c.sudo(
        f'bash -c "base64 -d <<<{encoded_contents} | kubectl apply -f-"', hide="both"
    )
    if ret.exited != 0:
        print(f"kubectl apply failed: {ret.stdout} / {ret.stderr}")
    return ret.exited == 0


def _delete_kubernetes_object(c: Connection, obj_type: str, obj_name: str) -> bool:
    ret = c.sudo(f"kubectl delete {obj_type} {obj_name} || true", hide="both")

    if len(ret.stderr) != 0 and "Error from server (NotFound)" not in ret.stderr:
        print(f"kubectl delete failed: {ret.stdout} / {ret.stderr}")
        return False

    return True


def _ensure_kubernetes_object(
    c: Connection, tool_name: str, obj: WebServiceConfig | NetworkPolicy
):
    if hasattr(obj, "delete") and obj.delete:
        return _delete_kubernetes_object(c, obj.k8s_type, obj.name)

    # Whack the object into the cluster
    print(f"Applying to {tool_name}: {obj}")
    return _apply_kubernetes_object(c, obj.as_k8s_object())


def _start_deployment(tool_name: str, deploy_token: str) -> str:
    r = requests.post(
        f"https://api.svc.toolforge.org/components/v1/tool/{tool_name}/deployment",
        params={"token": deploy_token},
    )
    r.raise_for_status()
    return r.json()["data"]["deploy_id"]


def _get_deployment_status(
    tool_name: str, deploy_id: str, deploy_token: str
) -> Optional[str]:
    r = requests.get(
        f"https://api.svc.toolforge.org/components/v1/tool/{tool_name}/deployment/{deploy_id}",
        params={"token": deploy_token},
    )
    if r.status_code == 409:
        print(
            f"Deployment already in progress for {tool_name} - multiple/queueing not supported currently!"
        )
        return None

    r.raise_for_status()
    return r.json()["data"]["status"]


def _execute_deployment(tool_name: str, deploy_token: str) -> bool:
    deploy_id = _start_deployment(tool_name, deploy_token)
    if deploy_id is None:
        return False

    print(f"Started deployment: {deploy_id}")
    while True:
        deployment_status = _get_deployment_status(tool_name, deploy_id, deploy_token)
        if deployment_status == "successful":
            print("Deployment has finished successfully")
            return True

        if deployment_status in ["pending", "running"]:
            print("Deployment is pending or in progress")
            time.sleep(1)
            continue

        print("Deployment is not pending, running or successful; probably failed")
        return False


def _generate_workflow(tool_name: str):
    # We do this to avoid `yaml` as a dep, it's simple enough
    config = f"name: 'Execute deployment for {tool_name}'\n"
    config += f"on: {{ push: {{ branches: [ main ], paths: [ "
    config += f"'{tool_name}.yaml', "
    config += f"'config/network-policies/{tool_name}.yaml', "
    config += f"'config/web-services/{tool_name}.yaml', "
    config += f"'.github/workflows/{tool_name}.yaml' "
    config += f"] }} }}\n"
    config += "jobs:\n"
    # Until T401868 is resolved, update the tool with the config we want deployed
    # Note: the config will not be re-fetched as `source_url` cannot be rewritten on the same sha... so this is '1 off'
    config += "  update-network-policies:\n"
    config += "    runs-on: ubuntu-latest\n"
    config += "    concurrency:\n"
    config += f"      group: {tool_name}\n"
    config += "    steps:\n"
    config += "      - uses: actions/checkout@v4\n"
    config += "      - uses: cluebotng/ci-execute-fabric@main\n"
    config += "        with:\n"
    config += f"          user: '{tool_name}'\n"
    config += "          task: update-network-policies\n"
    config += "          ssh_key: ${{ secrets.CI_SSH_KEY }}\n"

    config += "  update-component-config:\n"
    config += "    runs-on: ubuntu-latest\n"
    config += "    concurrency:\n"
    config += f"      group: {tool_name}\n"
    config += "    needs: [update-network-policies]\n"
    config += "    steps:\n"
    config += "      - uses: actions/checkout@v4\n"
    config += "      - uses: cluebotng/ci-execute-fabric@main\n"
    config += "        with:\n"
    config += f"          user: '{tool_name}'\n"
    config += "          task: update-component-config\n"
    config += "          ssh_key: ${{ secrets.CI_SSH_KEY }}\n"

    config += "  execute-deployment:\n"
    config += "    runs-on: ubuntu-latest\n"
    config += "    concurrency:\n"
    config += f"      group: {tool_name}\n"
    config += "    needs: [update-network-policies, update-component-config]\n"
    config += "    steps:\n"
    config += "      - uses: actions/checkout@v4\n"
    config += "      - uses: cluebotng/ci-execute-fabric@main\n"
    config += "        with:\n"
    config += f"          user: '{tool_name}'\n"
    config += "          task: execute-deployment\n"
    config += "          ssh_key: ${{ secrets.CI_SSH_KEY }}\n"

    config += "  update-webservice:\n"
    config += "    runs-on: ubuntu-latest\n"
    config += "    concurrency:\n"
    config += f"      group: {tool_name}\n"
    config += "    needs: [update-network-policies, update-component-config, execute-deployment]\n"
    config += "    steps:\n"
    config += "      - uses: actions/checkout@v4\n"
    config += "      - uses: cluebotng/ci-execute-fabric@main\n"
    config += "        with:\n"
    config += f"          user: '{tool_name}'\n"
    config += "          task: update-webservice\n"
    config += "          ssh_key: ${{ secrets.CI_SSH_KEY }}\n"
    return config


@task()
def create_workflows(_ctx):
    """Generate Github workflows for each tool"""
    for tool_name in _get_target_tools():
        workflow_file = (
            PosixPath(__file__).parent / ".github" / "workflows" / f"{tool_name}.yaml"
        )
        with workflow_file.open("w") as fh:
            fh.write(_generate_workflow(tool_name))


@task()
def update_component_config(_ctx):
    """Ensure the tool accounts have a component config setup."""
    for tool_name in _get_target_tools():
        c = _get_connection_for_tool(tool_name)
        if TARGET_USER is None or tool_name == TARGET_USER:
            print(f"Applying config for {tool_name}")
            _setup_component_configs(c, tool_name)


@task()
def execute_deployment(_ctx):
    """Execute a component deployment for a tool account."""
    # This can also be done externally using the deploy token, unfortunately when using environments in Github
    # actions, the shared secrets are not passed through, so to avoid having to copy the key into every environment,
    # fetch the deploy token from the tool account....
    for tool_name in _get_target_tools():
        if TARGET_USER is None or tool_name == TARGET_USER:
            c = _get_connection_for_tool(tool_name)
            if deploy_token := _get_deployment_token(c, tool_name):
                if not _execute_deployment(c, tool_name, deploy_token):
                    print(f"Deployment failed for {tool_name}")
                    if TARGET_USER:
                        # If we are executing for a single tool, the exit with a failure code
                        sys.exit(1)


@task()
def update_webservice(_ctx):
    """Execute a webservice deployment for a tool account."""
    webservices = _get_web_services()
    for tool_name in _get_target_tools():
        if TARGET_USER is None or tool_name == TARGET_USER:
            c = _get_connection_for_tool(tool_name)
            if webservice := webservices.get(tool_name):
                if not _ensure_kubernetes_object(c, tool_name, webservice):
                    print(f"Deployment failed for {tool_name}")
                    if TARGET_USER:
                        # If we are executing for a single tool, the exit with a failure code
                        sys.exit(1)


@task()
def update_network_policies(_ctx):
    """Execute a network policy deployment for a tool account."""
    network_policies = _get_network_policies()
    for tool_name in _get_target_tools():
        if TARGET_USER is None or tool_name == TARGET_USER:
            c = _get_connection_for_tool(tool_name)

            if network_policies := network_policies.get(tool_name):
                is_success = True
                for network_policy in network_policies:
                    is_success &= _ensure_kubernetes_object(
                        c, tool_name, network_policy
                    )
                if not is_success:
                    print(f"Deployment failed for {tool_name}")
                    if TARGET_USER:
                        # If we are executing for a single tool, the exit with a failure code
                        sys.exit(1)


@task()
def deploy(ctx):
    update_network_policies(ctx)
    execute_deployment(ctx)
    update_webservice(ctx)
