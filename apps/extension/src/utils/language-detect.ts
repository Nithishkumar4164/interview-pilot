/**
 * Simple heuristic-based programming language detection.
 */

interface LanguagePattern {
  language: string;
  patterns: RegExp[];
  weight: number;
}

const LANGUAGE_PATTERNS: LanguagePattern[] = [
  {
    language: 'python',
    patterns: [
      /\bdef\s+\w+\s*\(/,
      /\bimport\s+\w+/,
      /\bfrom\s+\w+\s+import\b/,
      /\bclass\s+\w+.*:/,
      /\bif\s+__name__\s*==\s*['"]__main__['"]/,
      /\bprint\s*\(/,
      /\bself\.\w+/,
      /:\s*$/m,
    ],
    weight: 1,
  },
  {
    language: 'javascript',
    patterns: [
      /\bfunction\s+\w+\s*\(/,
      /\bconst\s+\w+\s*=/,
      /\blet\s+\w+\s*=/,
      /\bconsole\.log\s*\(/,
      /=>\s*\{/,
      /\brequire\s*\(/,
      /\bmodule\.exports\b/,
    ],
    weight: 1,
  },
  {
    language: 'typescript',
    patterns: [
      /:\s*(string|number|boolean|void|any)\b/,
      /\binterface\s+\w+\s*\{/,
      /\btype\s+\w+\s*=/,
      /\bas\s+(string|number|boolean|any)\b/,
      /\bRecord<\w+,\s*\w+>/,
    ],
    weight: 1.5,
  },
  {
    language: 'java',
    patterns: [
      /\bpublic\s+(static\s+)?void\s+main\b/,
      /\bpublic\s+class\s+\w+/,
      /\bSystem\.out\.println\b/,
      /\bprivate\s+(static\s+)?\w+\s+\w+/,
      /\bnew\s+\w+(<[^>]+>)?\s*\(/,
      /\bimport\s+java\.\w+/,
      /\b(Map|List|Set|HashMap|ArrayList)<\w+/,
    ],
    weight: 1,
  },
  {
    language: 'cpp',
    patterns: [
      /\b#include\s*<\w+>/,
      /\bstd::\w+/,
      /\bcout\s*<</,
      /\bvector<\w+>/,
      /\bint\s+main\s*\(/,
      /\busing\s+namespace\s+std\b/,
    ],
    weight: 1,
  },
  {
    language: 'go',
    patterns: [
      /\bfunc\s+\w+\s*\(/,
      /\bpackage\s+\w+/,
      /\bfmt\.Print/,
      /:=\s*/,
      /\bgo\s+\w+\(/,
      /\bchan\s+\w+/,
    ],
    weight: 1,
  },
  {
    language: 'rust',
    patterns: [
      /\bfn\s+\w+\s*\(/,
      /\blet\s+mut\s+\w+/,
      /\bimpl\s+\w+/,
      /\b->\s*(i32|u32|String|bool|&str)\b/,
      /\bprintln!\s*\(/,
      /\b(Vec|Option|Result)<\w+>/,
    ],
    weight: 1,
  },
];

export function detectLanguage(code: string): string {
  if (!code.trim()) return 'unknown';

  const scores: Record<string, number> = {};

  for (const { language, patterns, weight } of LANGUAGE_PATTERNS) {
    let matchCount = 0;
    for (const pattern of patterns) {
      if (pattern.test(code)) {
        matchCount++;
      }
    }
    scores[language] = (matchCount / patterns.length) * weight;
  }

  let bestLang = 'unknown';
  let bestScore = 0.15; // minimum threshold

  for (const [lang, score] of Object.entries(scores)) {
    if (score > bestScore) {
      bestScore = score;
      bestLang = lang;
    }
  }

  return bestLang;
}
