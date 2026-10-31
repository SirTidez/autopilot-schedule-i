# Personal Schedule I fork, Apache-2.0. Compile source against local Unity APIs without starting Unity.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$EditorRoot,
    [Parameter(Mandatory)][string]$InputSystemAssembly,
    [Parameter(Mandatory)][string]$UIAssembly,
    [Parameter(Mandatory)][string]$TextMeshProAssembly
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$engine = Join-Path $EditorRoot 'Editor\Data\Managed\UnityEngine'
if (!(Test-Path -LiteralPath (Join-Path $engine 'UnityEngine.CoreModule.dll'))) {
    throw 'EditorRoot must be the installed Unity version directory containing Editor/Data/Managed/UnityEngine.'
}
$assemblies = @($InputSystemAssembly, $UIAssembly, $TextMeshProAssembly)
foreach ($assembly in $assemblies) {
    if (!(Test-Path -LiteralPath $assembly -PathType Leaf)) { throw "Missing local reference: $assembly" }
}
$output = Join-Path $repoRoot 'plugin\schedule-i\artifacts\unity-api-check'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$emptyFeed = Join-Path $output 'empty-feed'
New-Item -ItemType Directory -Path $emptyFeed -Force | Out-Null
function XmlText([string]$value) { [System.Security.SecurityElement]::Escape($value.Replace('\', '/')) }
$runtimeSource = XmlText (Join-Path $repoRoot 'plugin\unity\com.ramenvr.autopilot\Runtime\*.cs')
$editorSource = XmlText (Join-Path $repoRoot 'plugin\unity\com.ramenvr.autopilot\Editor\*.cs')
$references = @((Join-Path $engine '*.dll')) + $assemblies
$referenceXml = ($references | ForEach-Object {
    '<Reference Include="' + (XmlText $_) + '"><Private>false</Private></Reference>'
}) -join "`n"
$feed = XmlText $emptyFeed
$project = Join-Path $output 'Unity2022ApiCheck.csproj'
@"
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>netstandard2.1</TargetFramework>
    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>
    <GenerateAssemblyInfo>false</GenerateAssemblyInfo>
    <AllowUnsafeBlocks>true</AllowUnsafeBlocks>
    <LangVersion>latest</LangVersion>
    <AssemblyName>Autopilot.Editor.CompileCheck</AssemblyName>
    <DefineConstants>UNITY_EDITOR;UNITY_EDITOR_WIN;UNITY_STANDALONE_WIN;DEVELOPMENT_BUILD;UNITY_2022_1_OR_NEWER;UNITY_2022_2_OR_NEWER;UNITY_2022_3_OR_NEWER;ENABLE_INPUT_SYSTEM;ENABLE_LEGACY_INPUT_MANAGER;AUTOPILOT_INPUT_SYSTEM;AUTOPILOT_UGUI;AUTOPILOT_TMP;AUTOPILOT_UITK;AUTOPILOT_VIDEO;AUTOPILOT_PHYSICS;AUTOPILOT_PHYSICS2D;AUTOPILOT_PARTICLES;AUTOPILOT_SCREENCAPTURE;AUTOPILOT_IMAGECONVERSION</DefineConstants>
    <RestoreSources>$feed</RestoreSources>
    <NuGetAudit>false</NuGetAudit>
  </PropertyGroup>
  <ItemGroup>
    <Compile Include="$runtimeSource" />
    <Compile Include="$editorSource" />
    $referenceXml
  </ItemGroup>
</Project>
"@ | Set-Content -LiteralPath $project -Encoding utf8
Write-Output 'Source API check only: no Unity launch, package import, IL postprocessing or game installation.'
& dotnet build $project -c Release -m:1 -p:UseSharedCompilation=false -p:_EnableDefaultWindowsPlatform=false --nologo -v:minimal
if ($LASTEXITCODE -ne 0) { throw "Unity source API compilation failed ($LASTEXITCODE)." }
