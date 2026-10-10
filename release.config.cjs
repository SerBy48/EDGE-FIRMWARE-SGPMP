// Versionamiento automático (semantic-release), mismo esquema que
// BROKER-MQTT-SGPMP: la versión sale de los commits (Conventional Commits),
// develop publica release candidates (v0.3.0-rc.1) y main la versión final
// (v0.3.0). Las Raspberry solo instalan versiones finales de main
// (raspberry/scripts/update.sh). Procedimiento: docs/RELEASES.md.
//
// A diferencia del broker, solo main hace commit de CHANGELOG.md y de la
// versión del código: develop publica únicamente el tag del rc. Así el
// commit de release de main nunca choca con develop al volver a fusionarlo.
const onMain = process.env.GITHUB_REF_NAME === "main";

module.exports = {
  branches: ["main", { name: "develop", prerelease: "rc" }],
  plugins: [
    [
      "@semantic-release/commit-analyzer",
      {
        preset: "conventionalcommits",
        releaseRules: [
          { type: "feat", release: "minor" },
          { type: "fix", release: "patch" },
          { type: "perf", release: "patch" },
          { type: "refactor", release: "patch" },
          { type: "docs", release: false },
          { type: "chore", release: false },
          { type: "test", release: false },
          { type: "ci", release: false },
          { breaking: true, release: "major" },
        ],
      },
    ],
    ["@semantic-release/release-notes-generator", { preset: "conventionalcommits" }],
    ...(onMain
      ? [
          ["@semantic-release/changelog", { changelogFile: "CHANGELOG.md" }],
          [
            "@semantic-release/exec",
            { prepareCmd: "bash raspberry/scripts/set_version.sh ${nextRelease.version}" },
          ],
          [
            "@semantic-release/git",
            {
              assets: [
                "CHANGELOG.md",
                "raspberry/pyproject.toml",
                "raspberry/edge_agent/__init__.py",
              ],
              message: "chore(release): ${nextRelease.version} [skip ci]\n\n${nextRelease.notes}",
            },
          ],
        ]
      : []),
    ["@semantic-release/github", { successComment: false }],
  ],
};
