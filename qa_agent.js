// qa_agent.js
// Agente de QA iterativo usando la API de Anthropic
// Corrige código en un loop hasta que el agente confirme TESTS_COMPLETOS

const Anthropic = require("@anthropic-ai/sdk");

const client = new Anthropic({
  apiKey: process.env.ANTHROPIC_API_KEY,
});

// ─── System Prompt ────────────────────────────────────────────────────────────

const SYSTEM_PROMPT = `Eres un agente de QA experto en JavaScript y Python. Tu trabajo es corregir código iterativamente hasta que todos los tests pasen.

REGLAS:
1. Analiza el código y los errores de tests que recibes.
2. Identifica la causa raíz de cada error.
3. Devuelve SIEMPRE el código corregido completo (no fragmentos).
4. Explica brevemente qué corregiste y por qué.
5. Si todos los tests pasan o no hay errores que corregir, escribe TESTS_COMPLETOS al final de tu respuesta.
6. No escribas TESTS_COMPLETOS si aún quedan errores sin resolver.

FORMATO DE RESPUESTA:
---ANÁLISIS---
(qué está fallando y por qué)

---CORRECCIONES---
(qué cambiaste)

---CÓDIGO---
\`\`\`<lenguaje>
(código corregido completo)
\`\`\`

---ESTADO---
(TESTS_COMPLETOS si todo pasa, o ITERANDO si aún hay problemas)`;

// ─── Colores para consola ─────────────────────────────────────────────────────

const c = {
  reset:  "\x1b[0m",
  bold:   "\x1b[1m",
  cyan:   "\x1b[36m",
  green:  "\x1b[32m",
  yellow: "\x1b[33m",
  red:    "\x1b[31m",
  gray:   "\x1b[90m",
  blue:   "\x1b[34m",
};

function log(color, prefix, msg) {
  console.log(`${color}${c.bold}${prefix}${c.reset} ${msg}`);
}

function separator(label = "") {
  const line = "─".repeat(60);
  if (label) {
    console.log(`\n${c.cyan}${line}${c.reset}`);
    console.log(`${c.cyan}${c.bold}  ${label}${c.reset}`);
    console.log(`${c.cyan}${line}${c.reset}`);
  } else {
    console.log(`${c.gray}${line}${c.reset}`);
  }
}

// ─── Agente principal ─────────────────────────────────────────────────────────

/**
 * Ejecuta el agente de QA iterativamente.
 *
 * @param {string} code        - Código fuente a corregir
 * @param {string} testErrors  - Output de los tests (errores, stack traces, etc.)
 * @param {object} options
 * @param {number} options.maxIterations - Límite de iteraciones (default: 10)
 * @param {string} options.language      - Lenguaje del código (default: "javascript")
 * @returns {Promise<{finalCode: string, iterations: number, success: boolean}>}
 */
async function runQAAgent(code, testErrors, options = {}) {
  const { maxIterations = 10, language = "javascript" } = options;

  // Historial de mensajes para mantener contexto entre iteraciones
  const messages = [];

  let currentCode = code;
  let currentErrors = testErrors;
  let iteration = 0;
  let success = false;

  separator("🤖  AGENTE QA INICIANDO");
  log(c.blue, "Modelo:", "claude-sonnet-4-6");
  log(c.blue, "Máx. iteraciones:", String(maxIterations));
  log(c.blue, "Lenguaje:", language);
  console.log();

  while (iteration < maxIterations) {
    iteration++;
    separator(`ITERACIÓN ${iteration} / ${maxIterations}`);

    // Construir el mensaje del usuario para esta iteración
    const userMessage = iteration === 1
      ? buildInitialMessage(currentCode, currentErrors, language)
      : buildFollowUpMessage(currentCode, currentErrors);

    log(c.yellow, "👤 Input:", `código (${currentCode.split("\n").length} líneas) + errores`);
    if (currentErrors.trim()) {
      console.log(`${c.gray}Errores:\n${currentErrors.slice(0, 300)}${currentErrors.length > 300 ? "..." : ""}${c.reset}`);
    }

    messages.push({ role: "user", content: userMessage });

    // Llamar a la API
    let response;
    try {
      log(c.cyan, "🔄 Llamando a la API...", "");
      response = await client.messages.create({
        model: "claude-sonnet-4-6",
        max_tokens: 8096,
        system: SYSTEM_PROMPT,
        messages,
      });
    } catch (err) {
      log(c.red, "❌ Error API:", err.message);
      throw err;
    }

    const assistantText = response.content[0].text;

    // Agregar respuesta al historial
    messages.push({ role: "assistant", content: assistantText });

    // Mostrar respuesta completa
    console.log(`\n${c.blue}${c.bold}🤖 Respuesta del agente:${c.reset}`);
    console.log(assistantText);

    // Extraer código corregido de la respuesta
    const extractedCode = extractCode(assistantText, language);
    if (extractedCode) {
      currentCode = extractedCode;
      log(c.green, "✅ Código extraído:", `${extractedCode.split("\n").length} líneas`);
    }

    // Verificar si el agente declaró éxito
    if (assistantText.includes("TESTS_COMPLETOS")) {
      separator("✅  TESTS COMPLETADOS");
      log(c.green, "🎉 El agente confirmó:", "TESTS_COMPLETOS");
      log(c.green, "Iteraciones usadas:", String(iteration));
      success = true;
      break;
    }

    log(c.yellow, "⏳ Estado:", "ITERANDO — el agente continúa corrigiendo...");

    // En un caso real, aquí correrías los tests con el código nuevo
    // y pasarías los nuevos errores al siguiente ciclo.
    // Por ahora marcamos errores vacíos para simular que el usuario
    // actualiza currentErrors antes del siguiente loop.
    currentErrors = await promptForNewErrors();

    // Si no hay más errores, el agente lo confirmará en la próxima iteración
    if (!currentErrors.trim()) {
      log(c.green, "ℹ️  No se reportaron nuevos errores", "");
    }
  }

  if (!success) {
    separator("⚠️  LÍMITE DE ITERACIONES ALCANZADO");
    log(c.red, "❌ No se alcanzó TESTS_COMPLETOS en", `${maxIterations} iteraciones`);
  }

  return { finalCode: currentCode, iterations: iteration, success };
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function buildInitialMessage(code, errors, language) {
  return `Aquí está el código que necesito corregir y los errores de los tests:

**CÓDIGO (${language}):**
\`\`\`${language}
${code}
\`\`\`

**ERRORES DE TESTS:**
\`\`\`
${errors || "Sin errores reportados — verifica que el código sea correcto y confirma TESTS_COMPLETOS si todo está bien."}
\`\`\`

Por favor analiza y corrige el código.`;
}

function buildFollowUpMessage(code, errors) {
  return `Apliqué tu código corregido y corrí los tests nuevamente. Estos son los resultados:

**CÓDIGO ACTUAL:**
\`\`\`
${code}
\`\`\`

**NUEVOS ERRORES:**
\`\`\`
${errors || "No hay errores — todos los tests pasan."}
\`\`\`

Continúa corrigiendo si es necesario.`;
}

function extractCode(text, language) {
  // Intenta extraer el bloque de código del lenguaje especificado
  const patterns = [
    new RegExp("```" + language + "\\n([\\s\\S]*?)```", "i"),
    /```[\w]*\n([\s\S]*?)```/,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) return match[1].trim();
  }
  return null;
}

/**
 * En modo interactivo, espera nuevos errores del usuario (stdin).
 * En modo automático (e.g. CI), esta función debe reemplazarse por
 * la ejecución real de los tests.
 */
async function promptForNewErrors() {
  // Si se está ejecutando en modo no-interactivo (pipe o CI), retorna vacío
  if (!process.stdin.isTTY) return "";

  return new Promise((resolve) => {
    console.log(`\n${c.yellow}${c.bold}📋 Pega los nuevos errores de tests (Enter en línea vacía para terminar):${c.reset}`);
    let input = "";
    process.stdin.setEncoding("utf8");
    process.stdin.resume();
    process.stdin.on("data", (chunk) => {
      input += chunk;
      if (input.endsWith("\n\n")) {
        process.stdin.pause();
        resolve(input.trim());
      }
    });
  });
}

// ─── Modo demo / ejemplo de uso ───────────────────────────────────────────────

const DEMO_CODE = `
function sumar(a, b) {
  return a - b; // BUG: debería ser suma, no resta
}

function dividir(a, b) {
  return a / b; // BUG: no maneja división por cero
}

function esPalindromo(str) {
  return str === str.split("").reverse().join(""); // BUG: no ignora mayúsculas/espacios
}

module.exports = { sumar, dividir, esPalindromo };
`.trim();

const DEMO_ERRORS = `
FAIL functions.test.js
  ✕ sumar(2, 3) debería retornar 5 (3ms)
    Expected: 5
    Received: -1

  ✕ dividir(10, 0) debería lanzar error (1ms)
    Expected error to be thrown but function returned: Infinity

  ✕ esPalindromo("Anita lava la tina") debería retornar true (1ms)
    Expected: true
    Received: false

Tests: 3 failed, 0 passed
`.trim();

// ─── Entry point ──────────────────────────────────────────────────────────────

async function main() {
  if (!process.env.ANTHROPIC_API_KEY) {
    log(c.red, "❌ Error:", "ANTHROPIC_API_KEY no está definida en las variables de entorno");
    process.exit(1);
  }

  // Detectar si se pasa código/errores como argumentos o usamos el demo
  const useDemo = process.argv.includes("--demo") || process.argv.length < 4;

  let inputCode, inputErrors;

  if (useDemo) {
    log(c.yellow, "ℹ️  Modo DEMO:", "usando código y errores de ejemplo");
    inputCode = DEMO_CODE;
    inputErrors = DEMO_ERRORS;
  } else {
    // Uso: node qa_agent.js <archivo_codigo> <archivo_errores>
    const fs = require("fs");
    inputCode   = fs.readFileSync(process.argv[2], "utf8");
    inputErrors = fs.readFileSync(process.argv[3], "utf8");
  }

  const result = await runQAAgent(inputCode, inputErrors, {
    maxIterations: 10,
    language: "javascript",
  });

  separator("📊  RESUMEN FINAL");
  log(result.success ? c.green : c.red, "Estado:", result.success ? "✅ ÉXITO" : "❌ INCOMPLETO");
  log(c.blue,  "Iteraciones:", String(result.iterations));
  log(c.blue,  "Líneas de código final:", String(result.finalCode.split("\n").length));

  if (result.finalCode) {
    console.log(`\n${c.bold}Código final:${c.reset}`);
    console.log(result.finalCode);
  }
}

main().catch((err) => {
  console.error(`${c.red}Error fatal:${c.reset}`, err);
  process.exit(1);
});
