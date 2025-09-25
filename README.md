# Toolforge Component Configs

This repo holds the Toolforge component configs for ClueBot related tool accounts.

Reference: [https://wikitech.wikimedia.org/wiki/Help:Toolforge/Deploy_your_tool](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Deploy_your_tool).

## Deployments

We use the logic contained within `fabfile.py`, the component config is updated via ssh, then the deployment is triggered via HTTP.

Additional objects, such as the `Ingress` and `NetworkPolicy` objects are handled via SSH.

Any changes to a tool's configuration is picked up via GitHub actions and deployed using secrets contained at the GitHub org level.

Internal repos update the configuration on releases via a GitHub application, the key of which is contained at the GitHub org level (access granted per-repo).

Manual changes are essentially limited to new components, runtime resource changes, and object (`Ingress`/`NetworkPolicy`) changes.
