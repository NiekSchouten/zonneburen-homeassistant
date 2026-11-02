#!/usr/bin/env bash
# Zet het Zonneburen-logo in home-assistant/brands, zodat Home Assistant en HACS het tonen.
# Draai vanuit de hoofdmap van deze repo: bash brand/naar-brands.sh
set -euo pipefail

BRANDS=home-assistant/brands
EIGENAAR=$(gh api user -q .login)
FORK="$EIGENAAR/brands"
TAK=zonneburen

gh repo fork "$BRANDS" --clone=false >/dev/null 2>&1 || true
for poging in 1 2 3 4 5 6; do
  gh api "repos/$FORK" >/dev/null 2>&1 && break
  sleep 5
done
gh repo sync "$FORK" --branch master >/dev/null

basis=$(gh api "repos/$FORK/git/ref/heads/master" -q .object.sha)
gh api -X POST "repos/$FORK/git/refs" -f ref="refs/heads/$TAK" -f sha="$basis" >/dev/null 2>&1 || true

# Via een bestand: een afbeelding in base64 is te lang voor de opdrachtregel van Windows.
for bestand in icon.png icon@2x.png; do
  verzoek=$(mktemp)
  printf '{"message":"Add Zonneburen %s","branch":"%s","content":"%s"}' \
    "$bestand" "$TAK" "$(base64 -w0 "brand/$bestand")" > "$verzoek"
  gh api -X PUT "repos/$FORK/contents/custom_integrations/zonneburen/$bestand" --input "$verzoek" >/dev/null
  rm -f "$verzoek"
done

gh pr create -R "$BRANDS" --base master --head "$EIGENAAR:$TAK" \
  --title "Add Zonneburen custom integration" \
  --body "Adds the icon for the Zonneburen custom integration: https://github.com/NiekSchouten/zonneburen-homeassistant

- [x] The added/replaced image(s) are **PNG**
- [x] Icon image size is 256x256px (\`icon.png\`)
- [x] hDPI icon image size is 512x512px (\`icon@2x.png\`)
- [x] All images have transparent backgrounds
- [x] All images are trimmed and do not have unneeded space around them
- [x] The domain \`zonneburen\` matches the custom integration's manifest"
