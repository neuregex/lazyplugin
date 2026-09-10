"""Deterministic project pieces + the generation prompts.

The pom is OURS (never the model's): a wrong dependency line wastes a whole
compile round, so the only thing the LLM owns is plugin.yml + Java sources.
"""

# mc version -> paper-api artifact version (repo.papermc.io)
PAPER_API = {
    "1.20": "1.20.6-R0.1-SNAPSHOT",
    "1.21": "1.21.4-R0.1-SNAPSHOT",
}

POM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>io.lazyplugin</groupId>
  <artifactId>{artifact}</artifactId>
  <version>1.0.0</version>
  <packaging>jar</packaging>

  <properties>
    <maven.compiler.release>21</maven.compiler.release>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>

  <repositories>
    <repository>
      <id>papermc</id>
      <url>https://repo.papermc.io/repository/maven-public/</url>
    </repository>
  </repositories>

  <dependencies>
    <dependency>
      <groupId>io.papermc.paper</groupId>
      <artifactId>paper-api</artifactId>
      <version>{paper_version}</version>
      <scope>provided</scope>
    </dependency>
  </dependencies>

  <build>
    <finalName>{artifact}</finalName>
  </build>
</project>
"""

SYSTEM_PROMPT = """You are an expert Minecraft Paper plugin engineer. You write COMPLETE, COMPILING plugin projects for Paper {mc} on Java 21.

OUTPUT FORMAT — emit every file exactly like this, nothing else around them:
=== FILE: plugin.yml ===
<content>
=== FILE: src/main/java/io/lazyplugin/<pkg>/<Main>.java ===
<content>

RULES:
- Do NOT emit pom.xml (it is provided). Do NOT emit markdown fences around files.
- plugin.yml MUST declare: name, version, main (matching your main class), api-version: '{mc}', and every command you register (with description + usage) and every permission you check.
- Use the MODERN Paper API: messages are Adventure Components (net.kyori.adventure.text.Component) — `player.sendMessage(Component.text("...", NamedTextColor.GOLD))`, never legacy §-codes or deprecated String senders where a Component overload exists.
- Register commands with getCommand("name").setExecutor(...) and listeners with getServer().getPluginManager().registerEvents(...) in onEnable().
- World/entity access only on the main thread; use the scheduler to hop back from async.
- Keep it focused: implement exactly what the request asks, production-quality but no invented extras.
- If the request needs configuration, use config.yml with saveDefaultConfig() and emit the default config too. plugin.yml and config.yml belong under `src/main/resources/` (e.g. `=== FILE: src/main/resources/plugin.yml ===`) so Maven packs them into the jar.

REFERENCE RECIPES (verified Paper {mc} patterns — reuse these exact APIs where they apply):

{cards}
"""

FIX_PROMPT = """The project you generated failed to compile. Maven errors:

{errors}

Fix the code and RE-EMIT EVERY FILE, complete, in the same `=== FILE: path ===` format (all files, not only the changed ones). Do not emit pom.xml."""

EDIT_SYSTEM = """You are editing an EXISTING Minecraft Paper {mc} plugin (Java 21).

PROJECT MAP — every file and its symbols. Files whose full content is not shown below are UNCHANGED and must stay untouched:
{project_map}

FULL CONTENT of the files relevant to this edit:
{file_bodies}

OUTPUT FORMAT — emit ONLY the files you CHANGE or ADD, each complete, exactly like:
=== FILE: src/main/java/io/lazyplugin/<pkg>/<Class>.java ===
<full new content>

RULES:
- Never emit pom.xml. Changed files must be COMPLETE (no diffs, no "rest unchanged" fragments).
- If you add/rename a command or permission, RE-EMIT plugin.yml complete with it declared.
- Keep the existing package layout and style; change the minimum needed for the request.
- Modern Paper API only: Adventure Components, listeners/commands registered in onEnable.

REFERENCE RECIPES (verified Paper {mc} patterns — use where they apply):
{cards}
"""

EDIT_FIX_PROMPT = """The edit failed to compile. Maven errors:

{errors}
{extra_context}
Fix it and RE-EMIT ONLY the files you change, complete, in the same `=== FILE: path ===` format. Do not emit pom.xml."""
