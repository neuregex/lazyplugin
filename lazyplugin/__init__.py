"""LazyPlugin — describe a Minecraft server plugin, get a compiled Paper jar.

Pipeline: lexical RAG over a curated corpus of Paper API recipe cards -> your
model of choice writes the project (plugin.yml + Java sources; the pom is ours
and deterministic) -> Maven compiles -> a compile-error fix loop -> a jar, plus
a token and cost report for the run.

Bring your own model. Anything speaking the OpenAI chat protocol works, hosted
or local; see models.py. Nothing is hardcoded to one vendor.

    lazyplugin "give everyone a diamond every 5 minutes"
    lazyplugin serve
"""
__version__ = "0.1.0"
