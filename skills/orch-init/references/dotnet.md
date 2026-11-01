# .NET reference

verified: 2026-11-02
sources: <https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-new-sdk-templates>,
<https://learn.microsoft.com/en-us/nuget/consume-packages/central-package-management>,
<https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-format>, <https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-test>,
<https://learn.microsoft.com/en-us/dotnet/core/extensions/configuration>, <https://learn.microsoft.com/en-us/dotnet/core/extensions/options>,
<https://learn.microsoft.com/en-us/aspnet/core/security/app-secrets>, <https://learn.microsoft.com/en-us/dotnet/core/extensions/logging>,
<https://learn.microsoft.com/en-us/dotnet/core/project-sdk/msbuild-props> and `.../project-sdk/overview` (research digest), `gh api repos/actions/setup-dotnet/releases/latest` (2026-11-02).
Lines marked `unverified` have no primary source fetched. Architecture rules: `references/architecture.md`.

## Init command

These are for the first plan: `orch-init` writes docs and config only and does not run them. CLAUDE.md
`Commands` lists the ones that need code as `not yet runnable (no code)`.

```
dotnet new sln -n <App>            # slnx by default since .NET 10; --format sln|slnx (SDK 9.0.200+)
dotnet new gitignore && dotnet new editorconfig && dotnet new globaljson
dotnet new buildprops              # Directory.Build.props
dotnet new packagesprops           # Directory.Packages.props (central package versions)
dotnet new classlib -o src/<App>.Core
dotnet new console -o src/<App>.Cli   # or the web/worker template the project needs
dotnet sln add src/<App>.Core src/<App>.Cli
```

Template short names `sln`, `gitignore`, `editorconfig`, `globaljson`, `buildprops`, `classlib`, `console` are from the
SDK templates page; `packagesprops` from the central-package-management page. The xUnit/NUnit template names:
unverified (the page lists `mstest` templates; the test framework is a choice to ask or default to what the user's other repos use).

## Layout (interfaces and adapters)

- `src/<App>.Core`: domain and the interfaces (ports), **no package references**. `src/<App>.Infrastructure`: adapters
  with one `AddInfrastructure(this IServiceCollection)` extension. `src/<App>.Cli` (or `.Web`): the composition root,
  `Program.cs`, the only project that references Infrastructure and registers it (`AddApplicationServices()` for an optional `Application` layer).
- Project references are the boundary: `Core` references nothing, `Infrastructure` references `Core`, so the reverse is a
  compile error. Add a test that fails when `Core.csproj` gains a `PackageReference` or `ProjectReference`
  (read the XML; nothing external needed).
- Interfaces use core types and core exceptions only. Time is an `IUtcClock` interface, not `DateTime.UtcNow` in the core.
- Contract tests: an `abstract class StoreContract { protected abstract IStore Create(); [Fact] public void ... }` in
  `tests/<App>.Core.Tests`; each adapter's test project subclasses it (including the in-memory fake). Pattern is judgement.
- DI: `Microsoft.Extensions.DependencyInjection`, constructor injection, one registration extension per layer, documented order.
- Tests: `tests/<App>.<Layer>.Tests` per layer, mirroring folders.

## Tooling

- Format: `dotnet format` reads `.editorconfig`; `dotnet format --verify-no-changes` exits non-zero if anything would
  change; it may restore, compile and run analyzers, so run it only on trusted code (dotnet-format page).
- Build: `dotnet build`. Test: `dotnet test` runs VSTest by default; Microsoft.Testing.Platform (MTP) is available from the
  .NET 10 SDK (dotnet-test page). Single test (`--filter`): unverified.
- Packages: central package management. `Directory.Packages.props` sets `<ManagePackageVersionsCentrally>true</...>` and
  lists `<PackageVersion Include= Version= />`; project files use `<PackageReference Include="X" />` with no
  `Version` (NuGet CPM page). Several package sources then trigger NU1507; use package source mapping or one source.
- Analyzers: `AnalysisLevel`, `EnforceCodeStyleInBuild` in `Directory.Build.props` (MSBuild properties page, research digest).
- Per-file formatter for the hook (case patterns `*.cs`): `dotnet format whitespace --include <file>` (unverified: not fetched; `dotnet format` is slow per file and may restore, so prefer running it in the gate and skipping this hook if it lags).

## Strictness from day 0

`<Nullable>enable</Nullable>` and `<ImplicitUsings>` in `Directory.Build.props` (both appear in the MS configuration sample).
`TreatWarningsAsErrors` is a choice, not a Microsoft recommendation: turn it on in CI only
(`dotnet build -p:TreatWarningsAsErrors=true`, unverified) so local iteration stays quick. Pin the SDK in `global.json`.

## Logging

`ILogger<T>` through DI; message templates with named placeholders, not string interpolation (CA2254); `LoggerMessage`
source generation for hot paths (learn.microsoft.com .../extensions/logging). The sink is the logging provider chosen in the
composition root; Serilog bridged to `ILogger<T>` is a common pattern. Never log secrets
or user data; redact in the provider or an enricher (API: unverified); test with a sentinel.

## Errors

.NET exception convention for new projects: unverified (no primary page read). Working rule: typed exceptions in `Core`
(`DomainException` base), adapters translate vendor exceptions, a single catch-all in `Program.cs`. Expand-only data
migrations and confirmation before data mutation are project rules, not a convention.

## Config and secrets

Host default provider order, highest first: command line, environment variables, user secrets (Development only),
`appsettings.{Environment}.json`, `appsettings.json` (configuration page). Console apps do not get configuration by default;
the Generic Host (`Host.CreateApplicationBuilder`) is the recommended route. Bind to a typed options class and
validate at startup: `AddOptions<T>().Bind(...).ValidateDataAnnotations().ValidateOnStart()` (options page).
Development secrets: `dotnet user-secrets init` / `set "Key" "value"`; stored unencrypted in the user profile outside the
tree, for development only (app-secrets page). Environment variables use `__` for the hierarchy separator. No secret
in `appsettings.json`; `.env*`/secrets files ignored and denied (`templates/snippets.md`).

## Gate command

`dotnet format --verify-no-changes && dotnet build --no-restore -warnaserror && dotnet test --no-build`
(`-warnaserror` as a `dotnet build` switch: unverified; plus `./scripts/check-boundaries.sh` and `check-secrets.sh` when chosen).

## CI snippet notes

Write `ci.yml` only when the gate command exists after init; with no code yet, put "add CI once the gate exists" into `docs/STATE.md` next steps instead.

`actions/setup-dotnet` latest v6.0.0 (`gh api` 2026-11-02); `global-json-file` input: unverified. Then `dotnet restore`
and the gate. Windows-only projects (WPF/WinForms) need `runs-on: windows-latest` (unverified).

## Gotchas

- `dotnet new sln` makes `.slnx` on .NET 10 SDKs; older tooling may expect `.sln` (use `--format sln`).
- Only the first `Directory.Packages.props` up the tree is imported automatically; nested ones must import the parent (CPM page).
- User secrets are not encrypted and are not a production store (app-secrets page).
- `dotnet format` can execute analyzers from the project: trusted code only.
- Do not use `DateTime.UtcNow`/`Guid.NewGuid()` inside `Core`; inject the clock and id source for deterministic tests.
