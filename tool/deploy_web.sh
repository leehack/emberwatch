#!/bin/sh
# Builds the WebAssembly web app and publishes it to the gh-pages branch.
# Usage: tool/deploy_web.sh [remote]   (default: origin)
set -e
cd "$(dirname "$0")/.."
REMOTE="${1:-origin}"
URL="$(git remote get-url "$REMOTE")"
# GitHub Pages serves a project site from /<repository>/.
NAME="$(basename "$URL" .git)"
flutter build web --wasm --release --base-href "/$NAME/"
cd build/web
# Pages would otherwise run Jekyll, which drops files it does not recognise.
touch .nojekyll
rm -rf .git
git init -q -b gh-pages
# The build copies .gitignore files from the asset folders, and they would
# keep the compiled shaders and scenes out of the commit.
git add -A -f
git -c user.name="$(git -C ../.. config user.name)" \
    -c user.email="$(git -C ../.. config user.email)" \
    commit -q -m "Deploy $(git -C ../.. rev-parse --short HEAD)"
git push -q -f "$URL" gh-pages
rm -rf .git
