# Plan Base IA 3.0 — arquitectura de 99 agentes lógicos

## Objetivo
Organizar 99 roles lógicos especializados en tres células de 33, coordinados por un supervisor y ejecutados sobre la infraestructura existente. No son 99 procesos ni servicios independientes; los roles se ejecutan bajo demanda para mantener coste adicional objetivo de 0 €.

## Principios de seguridad
- No publicar contenido ni llamar a Metricool desde estos agentes.
- No aprobar ni saltarse el control técnico/editorial.
- No acceder a secretos, imprimir claves ni incluirlas en resultados.
- No ejecutar shell arbitrario, código remoto, ni acciones fuera de una lista permitida.
- No borrar ni reintentar en bucle trabajos existentes.
- No abrir PostgreSQL a Internet ni modificar producción como parte de esta propuesta.
- Cada agente produce resultados estructurados, con trazabilidad y estado; los resultados no verificados se etiquetan como pendientes.
- Límites de tiempo, tamaño de entrada, reintentos y concurrencia deben estar definidos antes de activar ejecución real.

## Coordinación
Supervisor/orquestador → 3 células → 33 roles por célula.
Los roles son definiciones de trabajo, no 99 instancias persistentes. La ejecución inicial será secuencial o con concurrencia limitada en el servicio Free.

## Célula A — Radar, fuentes y verificación (33)
1. Detector de tendencias
2. Lector RSS
3. Detector de duplicados
4. Clasificador temático
5. Detector de noticias urgentes
6. Analista de actualidad deportiva
7. Analista de motor y rutas
8. Analista de Fórmula 1
9. Analista de tecnología
10. Analista de inteligencia artificial
11. Analista de entretenimiento
12. Analista de música
13. Analista de meteorología local
14. Normalizador de titulares
15. Extractor de afirmaciones
16. Buscador de fuente primaria
17. Verificador de fecha
18. Verificador de entidades y nombres
19. Verificador de cifras
20. Verificador de citas
21. Comparador entre fuentes
22. Detector de contradicciones
23. Detector de rumor
24. Clasificador hecho/contexto/opinión
25. Evaluador de fiabilidad de fuente
26. Evaluador de relevancia para audiencia española
27. Detector de sensibilidad y riesgo
28. Detector de datos personales
29. Evaluador de derechos y atribución
30. Generador de ficha de evidencia
31. Priorizador de historias
32. Gestor de caducidad de noticia
33. Revisor final de evidencia

## Célula B — Guion, producción y control de calidad (33)
1. Generador de enfoque editorial
2. Generador de gancho inicial
3. Redactor de guion breve
4. Editor de claridad
5. Corrector ortográfico
6. Corrector gramatical
7. Verificador de nombres
8. Verificador de fechas
9. Verificador de números
10. Adaptador a español natural
11. Adaptador a duración de 10–15 s
12. Planificador de escenas
13. Generador de texto en pantalla
14. Verificador de zona segura vertical
15. Planificador de subtítulos
16. Revisor de legibilidad
17. Revisor de contraste
18. Revisor de ritmo
19. Revisor de llamada a la acción
20. Planificador de voz
21. Revisor de pronunciación
22. Revisor de sincronía audiovisual
23. Validador de formato 9:16
24. Validador de resolución 1080×1920
25. Validador de duración
26. Validador de pista de audio
27. Validador de decodificación
28. Detector de pantalla negra o imagen fija inesperada
29. Detector de marcas de agua y artefactos
30. Verificador de fuente, fecha y enlace
31. Revisor de coherencia guion-vídeo
32. Evaluador editorial final
33. Emisor de informe QC con bloqueo ante fallos

## Célula C — Operaciones, observabilidad, negocio y mejora (33)
1. Supervisor de cola
2. Analista de estados de trabajos
3. Detector de cola vacía
4. Clasificador de errores
5. Analista de fallos de proveedor
6. Comprobador de configuración no secreta
7. Analista de salud del servicio
8. Analista de latencia
9. Analista de consumo de recursos
10. Gestor de límites del plan gratuito
11. Detector de reintentos repetidos
12. Priorizador de trabajos recuperables
13. Gestor de trabajos bloqueados
14. Generador de informe operativo
15. Auditor de trazabilidad
16. Auditor de cambios
17. Validador de contratos API
18. Validador de respuestas HTTP
19. Analista de métricas de publicación (solo lectura)
20. Analista de métricas de audiencia (solo lectura)
21. Analista de rendimiento por tema
22. Analista de oportunidades de afiliación
23. Analista de oportunidades inmobiliarias
24. Analista de oportunidades de vehículos
25. Analista de oportunidades de gemas y metales
26. Analista de contenido de motor y rutas
27. Calculador de costes y retorno estimado
28. Detector de gasto no autorizado
29. Gestor de presupuesto objetivo cero
30. Generador de experimentos editoriales
31. Comparador de resultados de experimentos
32. Recomendador del siguiente paso
33. Auditor coordinador de salida

## Contrato común de agente
Cada rol debe declarar: id, nombre, célula, propósito, entradas permitidas, salida estructurada, validaciones, límites, riesgos y estado. Salidas recomendadas: `ok`, `needs_review`, `blocked`, `error`. Cada resultado incluye timestamp UTC, identificador de trabajo y evidencia o razón de bloqueo, sin secretos.

## Activación por fases
1. Catálogo estático y validación automática: comprobar que existen exactamente 99 IDs únicos y 33 roles por célula.
2. Ejecución en modo simulación sobre datos de prueba, sin llamadas externas ni cambios de estado.
3. Integración de lectura en el supervisor actual; comparar resultados y medir tiempo/recursos.
4. Activar únicamente roles que superen pruebas y límites en el servicio actual.
5. Solo después de pruebas reales completas del circuito, considerar automatización de publicación; QC técnico y editorial seguirán siendo obligatorios.

## Criterios de aceptación
- Exactamente 99 roles únicos, 33 por célula.
- Ningún rol puede publicar, aprobar QC ni saltarse el flujo.
- No hay secretos en logs o salidas.
- Duplicados y reintentos limitados.
- Modo simulación claramente distinguido del modo real.
- Sin nuevos servicios de pago, sin abrir la base de datos y sin despliegue automático.
- Pruebas de seguridad y regresión antes de cualquier despliegue.
