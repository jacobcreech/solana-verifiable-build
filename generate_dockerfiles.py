import subprocess
import os
import argparse
import requests
import tomllib


parser = argparse.ArgumentParser()
parser.add_argument("--upload", action="store_true")
parser.add_argument("--skip_cache", action="store_true")
parser.add_argument("--version")
args = parser.parse_args()

# Array of Solana version mapped to rust version hashes
RUST_DOCKER_IMAGESHA_MAP = {
    "1.68.0": "sha256:79892de83d1af9109c47a4566a24a0b240348bb8c088f1bccc52645c4c70ec39"
}


RUST_VERSION_PLACEHOLDER = "$RUST_VERSION"
SOLANA_VERSION_PLACEHOLDER = "$SOLANA_VERSION"

base_dockerfile_text = f"""
FROM --platform=linux/amd64 rust@{RUST_VERSION_PLACEHOLDER}

RUN apt-get update && apt-get install -qy git gnutls-bin
RUN sh -c "$(curl -sSfL https://release.solana.com/{SOLANA_VERSION_PLACEHOLDER}/install)"
ENV PATH="/root/.local/share/solana/install/active_release/bin:$PATH"
WORKDIR /build

CMD /bin/bash
"""

output = subprocess.check_output(
    ["git", "ls-remote", "--tags", "https://github.com/solana-labs/solana"]
)


def check_version(version_str):
    try:
        # Ignore this one
        if version_str == "v1.14.0":
            return False
        [major, minor, _patch] = version_str.strip("v").split(".")
        # Ignore 1.15.x
        return int(major) >= 1 and int(minor) >= 14 and int(minor) != 15
    except Exception as e:
        return False


def get_toolchain(version_tag):
    if "v1.14" in version_tag:
        return "1.68.0"

    url = f"https://raw.githubusercontent.com/solana-labs/solana/{version_tag}/rust-toolchain.toml"
    headers = {"Accept": "application/vnd.github.v3.raw"}  # Fetch the raw file content

    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        parsed_data = tomllib.loads(response.text)
        channel_version = parsed_data["toolchain"]["channel"]

        return channel_version
    else:
        print(f"Failed to fetch rust-toolchain.toml for {version_tag}")
        return None


tags = list(
    filter(
        check_version,
        [
            elem.split("\t")[1].split("/")[-1]
            for elem in output.decode("utf-8").split("\n")
            if elem
        ],
    )
)

dockerfiles = {}

dirty_set = set()
for release in tags:
    rust_version = get_toolchain(release)
    print("Generating Dockerfile for " + release + ", rust version " + rust_version)

    if rust_version is None:
        print(f"Failed to fetch rust version for {release}")
        continue

    if rust_version not in RUST_DOCKER_IMAGESHA_MAP and rust_version != "1.68.0":
        response = requests.get(
            f"https://hub.docker.com/v2/namespaces/library/repositories/rust/tags/{rust_version}"
        )

        if response.status_code == 200:
            # JSONify response
            response_json = response.json()

            # find amd64 image
            for image in response_json["images"]:
                if image["architecture"] == "amd64":
                    RUST_DOCKER_IMAGESHA_MAP[rust_version] = image["digest"]
                    break

            if rust_version not in RUST_DOCKER_IMAGESHA_MAP:
                print(f"Failed to fetch rust image for {rust_version}")
                continue

    dockerfile = base_dockerfile_text.replace(
        SOLANA_VERSION_PLACEHOLDER, release
    ).lstrip("\n")
    dockerfile = dockerfile.replace(
        RUST_VERSION_PLACEHOLDER, RUST_DOCKER_IMAGESHA_MAP[rust_version]
    )

    path = f"docker/{release}.Dockerfile"
    
    # Check if the file exists before trying to read it
    if os.path.exists(path):
        with open(path, "r") as f:
            prev = f.read()
        
        if prev != dockerfile:
            dirty_set.add(release.strip("v"))
            print(f"{release} needs to be updated")
    else:
        # If the file doesn't exist, consider it as dirty
        dirty_set.add(release.strip("v"))
        print(f"{release} is new and needs to be created")

    # Write the new or updated Dockerfile
    with open(path, "w") as f:
        f.write(dockerfile)
    
    dockerfiles[release] = path

print(RUST_DOCKER_IMAGESHA_MAP)

digest_set = set()
if not args.skip_cache:
    print("Fetching existing images")
    response = requests.get(
        "https://hub.docker.com/v2/namespaces/ellipsislabs/repositories/solana/tags?page_size=1000"
    )
    for result in response.json()["results"]:
        print(result)
        if result["name"] != "latest":
            try:
                digest_set.add(result["name"])
            except Exception as e:
                print(e)
                continue

if args.upload:
    print("Uploading all Dockerfiles")
    for tag, dockerfile in dockerfiles.items():
        # Strip the `v` from the tag to keep the versions consistent in Docker
        stripped_tag = tag.strip("v")

        (major, minor, patch) = stripped_tag.split(".")

        print(stripped_tag, args.version)

        force_build = False
        if args.version is not None:
            ver = args.version.split(".")
            if len(ver) == 2:
                a_major, a_minor = ver
                a_patch = patch
            if len(ver) == 3:
                a_major, a_minor, a_patch = ver
            if major != a_major or minor != a_minor or a_patch != patch:
                print(f"Skipping {stripped_tag}")
                continue
            force_build = True

        if (
            stripped_tag in digest_set
            and stripped_tag not in dirty_set
            and not force_build
        ):
            print(f"Already built image for {stripped_tag}, skipping")
            continue
        if stripped_tag in dirty_set:
            print(f"Dockerfile for {stripped_tag} needs to be modified")
        version_tag = f"solana:{stripped_tag}"
        print(version_tag)
        current_directory = os.getcwd()
        res = subprocess.call(
            f"docker build -t {version_tag} - < {current_directory}/{dockerfile}",
            shell=True,
        )
        if res == 0:
            subprocess.call(
                f"docker tag {version_tag} ellipsislabs/{version_tag}", shell=True
            )
            subprocess.call(f"docker push ellipsislabs/{version_tag}", shell=True)
        else:
            continue
