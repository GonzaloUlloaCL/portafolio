# 05 · Red de agentes con memoria (2026)

Trabajo a diario con agentes de IA (Claude Code). El problema: cada sesión empieza sin memoria, y una IA sin reglas
afirma cosas que no verificó. Esta red es la solución que diseñé: una base de conocimiento en Markdown, con reglas que el
código hace cumplir.

## Cómo está hecha

- **[Ontología](ONTOLOGIA.md):** 5 tipos de nodo según **cómo caducan** (proyecto, sistema, criterio, evidencia,
  persona) y 5 verbos para relacionarlos. Nació de corregir una red anterior con 45 tipos que nadie podía mantener.
- **[Linter](herramientas/linter_red.py):** valida los nodos (hoy 159) y sus relaciones antes de cada cierre.
- **[Protocolo de cierre](PROTOCOLO.md):** "se resta antes de sumar". El protocolo anterior solo agregaba y la red se
  degradó igual.
- **[Principios](principios_alma.md):** honestidad por encima de todo; no presentar lo incierto como hecho.

## Candados: reglas que viven en código, no en un documento

Una regla escrita se rompe; una que el código hace cumplir, no.

| Candado | Qué bloquea |
|---|---|
| [`candado_hechos.py`](herramientas/candado_hechos.py) | Afirmaciones sobre mí que no están verificadas, por ejemplo una cifra de años de experiencia inflada |
| [`candado_postulacion.py`](hooks/candado_postulacion.py) | Que un agente envíe una postulación sin haber leído completa la oferta |
| [`candado_espanol_neutro.py`](hooks/candado_espanol_neutro.py) | Respuestas fuera del español neutro acordado |

Cada candado trae sus pruebas (`test_*.py`).

## Agentes especializados

- [Skill `tour-obra-3d`](skills/tour-obra-3d/) y [agente `revisor-tour`](agentes/revisor-tour.md): arman y auditan los
  recorridos 3D de las obras de la etapa 06 (huecos sin techo, ventanas mal leídas, alturas que asoman).
