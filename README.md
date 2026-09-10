# LazyPlugin

Describe a Minecraft server plugin in plain English, get a compiled Paper jar.

```bash
lazyplugin "every player who mines diamond ore gets a firework and a message in chat"
```

```
  * retrieved 8 recipe cards: events-join-message, cmd-main-class-skeleton, ...
  * attempt 1: calling the model...
  * attempt 1: compile FAILED (27 error lines)
  * attempt 2: calling the model...
  * compiled OK -> target/diamond-firework.jar

  files      : 4
  tokens     : 9086 in / 1732 out
  est. cost  : $0.0045
  duration   : 18.4s
```

Drop the jar in your `plugins/` folder and restart. That run is real output, including
the first attempt failing to compile: LazyPlugin reads the compiler errors and fixes
its own code, which is most of why this produces working jars instead of plausible Java.

## Bring your own model

There is no vendor lock-in and no account to create. LazyPlugin speaks the OpenAI
chat protocol, which today means nearly everything, hosted or running on your own GPU.

| Provider | Set this | Example |
|---|---|---|
| **Ollama** (local, free) | nothing | `--provider ollama --model qwen2.5-coder:32b` |
| **LM Studio** (local, free) | nothing | `--provider lmstudio --model your-loaded-model` |
| **vLLM** (local, free) | nothing | `--provider vllm --model ...` |
| DeepSeek | `DEEPSEEK_API_KEY` | `--model deepseek/deepseek-v4-flash` |
| OpenAI | `OPENAI_API_KEY` | `--model openai/gpt-4o` |
| Anthropic | `ANTHROPIC_API_KEY` | `--model anthropic/claude-sonnet-5` |
| Google Gemini | `GEMINI_API_KEY` | `--model google/gemini-2.5-pro` |
| Groq | `GROQ_API_KEY` | `--model groq/llama-3.3-70b-versatile` |
| OpenRouter | `OPENROUTER_API_KEY` | `--model openrouter/anthropic/claude-sonnet-5` |
| Mistral, xAI, Together, Fireworks | see `lazyplugin models` | |
| Anything else | | `--base-url https://your-gateway/v1 --key ...` |

The table is a starting point, not a whitelist. Any model id is passed through
untouched, so something released this morning works this morning.

Rather than take our word for which providers work, ask your own machine:

```bash
lazyplugin models --check
```

```
+ deepseek     ok                           2
  openai       no key (OPENAI_API_KEY)
  ollama       not running
```

**No API key at all?** Run a model locally and it stays free forever:

```bash
ollama serve
ollama pull qwen2.5-coder:32b
lazyplugin --provider ollama --model qwen2.5-coder:32b "a /warp command with cooldowns"
```

Smaller local models need more fix rounds, and sometimes give up. That is what
`--fix-rounds` is for.

## Install

Needs Python 3.10+, a JDK 17+, and Maven.

```bash
pip install lazyplugin          # add [web] for the browser interface
cp .env.example .env            # put one API key in it, or skip for local models
```

If Maven is not on your `PATH`, point at it with `LAZYPLUGIN_MAVEN=/path/to/mvn`.

## The web interface

```bash
lazyplugin serve
```

Opens a local page with a model picker, a box to describe the plugin, live build
progress and a download button. It binds to `127.0.0.1` on purpose: the process
holds your API keys, so anyone who can reach the port can spend your credits.

## Commands

```bash
lazyplugin "a plugin that ..."                 # create
lazyplugin edit ./my-project "add a cooldown"  # change an existing one
lazyplugin models                              # what you can use
lazyplugin models --check                      # what actually answers
lazyplugin serve                               # web interface
```

Useful flags: `--version 1.21`, `--out DIR`, `--fix-rounds 3`, `--key`, `--base-url`.

## How it works

1. **Retrieve.** A curated corpus of Paper API recipe cards (`corpus.json`) is
   searched with plain keyword scoring. For a corpus this size, deterministic
   ranking beats an embedding stack: no index to build, and you can read exactly
   why a card was chosen.
2. **Generate.** The model writes `plugin.yml` and the Java sources. The `pom.xml`
   is ours and deterministic, so a hallucinated dependency cannot break the build.
3. **Compile.** Maven builds it for real.
4. **Fix.** Compiler errors go back to the model, up to `--fix-rounds` times. This
   step is what separates a jar from a guess.
5. **Report.** Tokens, cost and duration for every run.

## Adding a provider

Five lines in `lazyplugin/models.py`:

```python
Provider("myprovider", "My Provider", "https://api.example.com/v1",
         "MYPROVIDER_API_KEY", "https://example.com/docs"),
```

Pull requests welcome, especially for local runners and for recipe cards covering
Paper APIs the corpus misses.

## Do not want to install anything?

[LazySteve](https://lazysteve.io) runs this hosted, with no key, no JDK and no Maven.
It also does the same trick for Minecraft *builds*: describe a castle, get a
schematic. LazyPlugin is the plugin half, open sourced.

## License

MIT. Use it commercially, fork it, ship it inside your own tool.
