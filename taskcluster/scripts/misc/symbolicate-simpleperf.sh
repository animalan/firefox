#!/bin/bash
set -x -e -v

ARTIFACT_DIR="$MOZ_FETCHES_DIR/../artifacts"
WORK_DIR="pre-symbolication"
SCRIPT_NAME="${1}"
TEST_NAME=$(awk -F': ' '/^#name:/ { print $2 }' "$SCRIPT_NAME")
OUTPUT_DIR="profile_$TEST_NAME"
BREAKPAD_SYMBOL_DIR="$MOZ_FETCHES_DIR/target.crashreporter-symbols"
BREAKPAD_SYMBOL_SERVER="https://symbols.mozilla.org/"
READ_TIMEOUT="60" # max wait time for Samply to generate profiler URL

# Uncompress crashreporter symbols
unzip "${BREAKPAD_SYMBOL_DIR}.zip" -d "$BREAKPAD_SYMBOL_DIR"

mkdir -p "$WORK_DIR" # staging (pre-symbolication) directory
mkdir -p "$OUTPUT_DIR" # store symbolicated profile.json files

# Add dependencies to path
export PATH=$PATH:$MOZ_FETCHES_DIR/samply
export PATH="$MOZ_FETCHES_DIR/node/bin:$PATH"

# Extract perf data
tar -xvzf "$ARTIFACT_DIR/$TEST_NAME.tgz" --wildcards '*.data'

# Generate profile.json files from perf data
for file in $TEST_NAME/*.data; do
    filename=$(basename "$file") # e.g. perf-1.data
    number=${filename//[!0-9]/} # extract number, strips all non-numeric characters
    samply import "$file" --save-only -o "$WORK_DIR/profile-$number-unsymbolicated.json"
done

# Symbolicate all profiles
for file in "$WORK_DIR"/*.json; do

    filename=$(basename "$file" .json)
    base="${filename%-unsymbolicated}"
    temp_file="${base}_output.tmp"

    # Launch samply load in the background and redirect output to tempfile
    samply load "$file" --no-open \
        --breakpad-symbol-dir "$BREAKPAD_SYMBOL_DIR" \
        --breakpad-symbol-server "$BREAKPAD_SYMBOL_SERVER" \
        > "$WORK_DIR/$temp_file" &

    samply_pid=$!

    # Wait maximum READ_TIMEOUT seconds for profiler URL from samply. Tailing the tempfile
    # should be more robust/efficient than manual polling.
    read -t $READ_TIMEOUT url < <(tail -n +1 -F "$WORK_DIR/$temp_file" | grep --line-buffered -m 1 'https://profiler.firefox.com/')

    # Convert percent-encoded url to hex-encoding (allowing decoding with %b)
    decoded_url="$(printf '%b' "${url//%/\\x}")" # /%/\\x replaces % with \\x

    # Extract server url ("http://127.0.0.1:port/...") from decoded url
    match=$(echo "$decoded_url" | grep -o 'symbolServer=[^"]*')
    server_url=${match#symbolServer=} # Remove prefix

    # Symbolicate profile.json
    node "$MOZ_FETCHES_DIR/symbolicator-cli/symbolicator-cli.js" \
        --input "$file" \
        --output "$OUTPUT_DIR/$base.json" \
        --server "$server_url"

    # Clean up samply process
    kill -SIGINT "$samply_pid"
    wait "$samply_pid"
done

# Optionally, upload unsymbolicated profile.json files
cp "$WORK_DIR"/*.json "$OUTPUT_DIR"

# Compress, archive, and upload symbolicated profile.json files
find "$OUTPUT_DIR" -type f -name '*.json' | sort -V | zip -@ "$ARTIFACT_DIR/$OUTPUT_DIR.zip"
