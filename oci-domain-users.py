import argparse
import json
import sys
import time

import oci
import requests
from oci.identity import IdentityClient
from oci.identity_domains import IdentityDomainsClient
from oci.pagination import list_call_get_all_results
from oci.retry import DEFAULT_RETRY_STRATEGY


METADATA_URL = "http://169.254.169.254/opc/v2/instance/"
METADATA_HEADERS = {"Authorization": "Bearer Oracle"}
PAGE_SIZE = 100


def parse_args():
    # Parse optional flags while keeping interactive mode as the default.
    parser = argparse.ArgumentParser(description="List users from an OCI IAM identity domain.")
    parser.add_argument("--domain-ocid", help="Skip the prompt and use this identity domain OCID.")
    parser.add_argument("--format", choices=("table", "json"), default="table", help="Output format.")
    parser.add_argument("--user-limit", type=int, help="Maximum number of users to print.")
    return parser.parse_args()


def info(message):
    # Print a consistent informational message.
    print(f"[INFO] {message}", file=sys.stderr)


def warn(message):
    # Print a consistent warning message.
    print(f"[WARN] {message}", file=sys.stderr)


def measure_latency(url, timeout=3):
    # Return endpoint latency in milliseconds, or None when it cannot be reached.
    try:
        start = time.perf_counter()
        requests.get(url, timeout=timeout, verify=True)
        return round((time.perf_counter() - start) * 1000)
    except requests.RequestException:
        return None


def get_instance_region():
    # Read the compute metadata service to discover the current OCI region.
    response = requests.get(METADATA_URL, headers=METADATA_HEADERS, timeout=5)
    response.raise_for_status()
    region = response.json().get("canonicalRegionName")
    if not region:
        raise RuntimeError("Region not found in instance metadata.")
    return region


def get_identity_client(signer, region):
    # Build an IAM client with instance principal auth and SDK retries.
    client = IdentityClient(config={}, signer=signer, retry_strategy=DEFAULT_RETRY_STRATEGY)
    client.base_client.set_region(region)
    return client


def get_all_results(list_func, **kwargs):
    # Fetch every page from a standard OCI list API.
    response = list_call_get_all_results(list_func, **kwargs)
    return response.data or []


def get_all_compartments(identity_client, tenancy_id):
    # Recursively return active compartments accessible to the instance principal.
    compartments = []

    def fetch_children(parent_id):
        # Walk one parent compartment and then recurse into its children.
        try:
            children = get_all_results(
                identity_client.list_compartments,
                compartment_id=parent_id,
                access_level="ACCESSIBLE",
                compartment_id_in_subtree=False,
                lifecycle_state="ACTIVE",
            )
        except oci.exceptions.ServiceError as exc:
            warn(f"Could not list compartments under {parent_id}: {exc}")
            return

        for compartment in children:
            compartments.append(compartment)
            fetch_children(compartment.id)

    fetch_children(tenancy_id)
    return compartments


def discover_domains(identity_client, tenancy_id):
    # Search the tenancy root and all accessible compartments for identity domains.
    compartments = [(tenancy_id, "Tenancy Root")]
    compartments.extend((c.id, c.name) for c in get_all_compartments(identity_client, tenancy_id))
    domains = []

    info(f"Total compartments found: {len(compartments)}")
    for compartment_id, compartment_name in compartments:
        try:
            for domain in get_all_results(identity_client.list_domains, compartment_id=compartment_id):
                domains.append((compartment_name, domain))
        except oci.exceptions.ServiceError as exc:
            if exc.status != 404:
                warn(f"Could not list domains in compartment {compartment_name}: {exc}")

    return domains


def prompt_for_domain(domains):
    # Ask the operator to choose one discovered identity domain.
    if not sys.stdin.isatty():
        raise RuntimeError("Use --domain-ocid when running non-interactively.")

    print("\nAvailable Identity Domains:")
    for idx, (compartment_name, domain) in enumerate(domains, start=1):
        print(f"{idx}. {domain.display_name} ({domain.lifecycle_state}) [Compartment: {compartment_name}]")

    while True:
        try:
            choice = int(input("\nEnter the number of the domain you want to work on: "))
            if 1 <= choice <= len(domains):
                return domains[choice - 1]
        except ValueError:
            pass
        print("Invalid choice. Please enter a valid number.")


def get_selected_domain(identity_client, tenancy_id, domain_ocid):
    # Resolve the selected identity domain from CLI input or an interactive prompt.
    if domain_ocid:
        return None, identity_client.get_domain(domain_ocid).data

    domains = discover_domains(identity_client, tenancy_id)
    if not domains:
        raise RuntimeError("No identity domains found in accessible compartments.")
    return prompt_for_domain(domains)


def choose_domain_endpoint(domain, instance_region):
    # Measure domain endpoints and choose the lowest-latency reachable URL.
    candidates = [(domain.home_region, domain.home_region_url, "home")]
    candidates.extend((r.region, r.regional_url, "replica") for r in (domain.replica_regions or []))

    measured = []
    for region, url, kind in candidates:
        if not url:
            continue
        measured.append(
            {
                "region": region,
                "url": url,
                "kind": kind,
                "latency_ms": measure_latency(url),
            }
        )

    if not measured:
        raise RuntimeError("No usable regional URL found for the selected domain.")

    reachable = [item for item in measured if item["latency_ms"] is not None]
    selected = (
        min(reachable, key=lambda item: (item["latency_ms"], item["region"] != instance_region))
        if reachable
        else measured[0]
    )
    return selected, measured


def print_endpoint_summary(domain, measured, selected):
    # Show the domain home region, replicas, and selected endpoint.
    print(f"\n[INFO] Home region of domain: {domain.home_region}", file=sys.stderr)
    print(f"[INFO] Home region URL: {domain.home_region_url}", file=sys.stderr)
    print("\n[INFO] Available domain endpoints:", file=sys.stderr)

    for item in measured:
        latency = f"{item['latency_ms']}ms" if item["latency_ms"] is not None else "unreachable"
        print(f"  - {item['region']} ({item['kind']}) | Latency: {latency}", file=sys.stderr)

    print(f"\n[INFO] Using region: {selected['region']}", file=sys.stderr)
    print(f"[INFO] Regional URL: {selected['url']}", file=sys.stderr)


def get_identity_domain_client(signer, endpoint):
    # Build the Identity Domains client for the selected domain endpoint.
    return IdentityDomainsClient(
        config={},
        signer=signer,
        service_endpoint=endpoint,
        retry_strategy=DEFAULT_RETRY_STRATEGY,
    )


def list_users(identity_domain_client, user_limit=None):
    # Fetch users from the identity domain with SCIM-style pagination.
    users = []
    start_index = 1

    while True:
        response = identity_domain_client.list_users(start_index=start_index, count=PAGE_SIZE)
        data = response.data
        resources = getattr(data, "resources", None) or []
        users.extend(resources)

        if user_limit and len(users) >= user_limit:
            return users[:user_limit]

        total_results = getattr(data, "total_results", None)
        if not resources or (total_results is not None and len(users) >= total_results):
            return users

        start_index += len(resources)


def print_users(users, output_format):
    # Print selected user fields in table or JSON format.
    if output_format == "json":
        print(
            json.dumps(
                [
                    {
                        "user_name": getattr(user, "user_name", None),
                        "id": getattr(user, "id", None),
                        "active": getattr(user, "active", None),
                    }
                    for user in users
                ],
                indent=2,
            )
        )
        return

    print(f"\nFound {len(users)} users:")
    for user in users:
        print(f"  - {getattr(user, 'user_name', None)} ({getattr(user, 'id', None)})")


def main():
    # Run domain selection, endpoint choice, and user listing from an OCI compute instance.
    args = parse_args()

    info("Detecting instance region from metadata...")
    instance_region = get_instance_region()
    info(f"Instance region: {instance_region}")

    info("Initializing instance principal signer...")
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    tenancy_id = signer.tenancy_id
    info(f"Tenancy OCID: {tenancy_id}")

    identity_client = get_identity_client(signer, instance_region)
    info(f"Using Identity service endpoint: https://identity.{instance_region}.oraclecloud.com")

    selected_compartment, selected_domain = get_selected_domain(identity_client, tenancy_id, args.domain_ocid)
    if not args.domain_ocid:
        selected_domain = identity_client.get_domain(selected_domain.id).data

    info(f"Selected domain: {selected_domain.display_name}")
    info(f"Domain OCID: {selected_domain.id}")
    if selected_compartment:
        info(f"Compartment: {selected_compartment}")

    selected_endpoint, measured_endpoints = choose_domain_endpoint(selected_domain, instance_region)
    print_endpoint_summary(selected_domain, measured_endpoints, selected_endpoint)

    info("Fetching users from Identity Domain...")
    identity_domain_client = get_identity_domain_client(signer, selected_endpoint["url"])
    print_users(list_users(identity_domain_client, args.user_limit), args.format)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit("\n[ERROR] Interrupted.")
    except Exception as exc:
        raise SystemExit(f"[ERROR] {exc}")
