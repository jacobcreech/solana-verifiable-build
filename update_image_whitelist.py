import re
import requests
import os

github_token = os.environ.get('GITHUB_TOKEN')
use_ghcr = os.environ.get('USE_GHCR', 'false').lower() == 'true'
headers = {'Authorization': f'Bearer {github_token}'}

if use_ghcr:
    response = requests.get(
        "https://api.github.com/users/ngundotra/packages/container/solana/versions?per_page=100",
        headers=headers
    )
    if response.status_code != 200:
        raise Exception(f"Failed to get Docker images: {response.status_code} {response.text}") 
    results = response.json()
else:
    response = requests.get(
        "https://hub.docker.com/v2/namespaces/ellipsislabs/repositories/solana/tags?page_size=1000"
    )
    if response.status_code != 200:
        raise Exception(f"Failed to get Docker images: {response.status_code} {response.text}") 
    results = response.json()["results"] 

    sfResponse = requests.get(
        "https://hub.docker.com/v2/namespaces/solanafoundation/repositories/solana-verified-build/tags?page_size=1000"
    )
    if sfResponse.status_code != 200:
        raise Exception(f"Failed to get Docker images: {sfResponse.status_code} {sfResponse.text}") 
    results = sfResponse.json()["results"] + results

digest_map = {}
for result in results:
    if use_ghcr:
        # For GHCR, extract version from metadata
        metadata = result.get("metadata", {})
        container = metadata.get("container", {})
        tags = container.get("tags", [])
        for tag in tags:
            match = re.match(r'(\d+)\.(\d+)\.(\d+)', tag)
            if match:
                major, minor, patch = map(int, match.groups())
                digest_map[(major, minor, patch)] = result["name"]  # "name" contains the digest for GHCR
                break 
    else:
        if result["name"] != "latest":
            try:
                major, minor, patch = list(map(int, result["name"].split(".")))
                digest_map[(major, minor, patch)] = result["digest"]
            except Exception as e:
                print(e)
                continue


entries = []
for k, v in sorted(digest_map.items()):
    entries.append(f'        m.insert({k}, "{v}");')

mappings = "\n".join(entries)

code = f"""
/// THIS FILE IS AUTOGENERATED. DO NOT MODIFY
use lazy_static::lazy_static;
use std::collections::BTreeMap;

lazy_static! {{
    pub static ref IMAGE_MAP: BTreeMap<(u32, u32, u32), &'static str> = {{
        let mut m = BTreeMap::new();
{mappings}
        m
    }};
}}
"""

print(code)

with open("src/image_config.rs", "w") as f:
    f.write(code.lstrip("\n"))
