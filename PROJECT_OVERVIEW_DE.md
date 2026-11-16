# Projektübersicht — ML-Demand-Forecasting-Co-Pilot für S&OP eines britischen Konsumgüterherstellers

Ein vollständiger Leitfaden zum System: was es tut, wie es funktioniert, was Danny damit macht und welche Probleme es löst.

---

# 1. Was das Projekt ist

Das Projekt ist eine **ausnahmebasierte S&OP-Arbeitsumgebung (Sales & Operations Planning)** für ein britisches Konsumgüterunternehmen. Es verarbeitet rund 33,5 Millionen Zeilen historischer Daten (20 Parquet-Tabellen: Artikelstammdaten, monatliche Nachfrage, Amazon-Tagesdaten zu Traffic/Sell-out/Inventar, Auftragsbuch, Aktionen, Lebenszyklus) und verwandelt sie in eine **monatliche Planungspipeline**, die ein einzelner Planer betreiben kann.

Statt jeden Monat 9.173 Produkt-Kanal-Kombinationen zu prüfen, prüft der Planer **25–100 Ausnahmen** — die Positionen, bei denen die Maschinenprognose um einen materiellen Geldbetrag von der Baseline abweicht. Jede Entscheidung wird **auditiert** (wer, was, wann, warum) und mit **FVA** (Forecast Value Added) **bewertet**, sodass das Urteilsvermögen des Planers messbar wird — nicht nur vertraut.

Das System ist über die Kern-Arbeitsumgebung hinaus zu einem vollständigen **täglichen Task-Hub und Co-Pilot** gewachsen: ein Chatbot, der Fragen aus den Live-Daten beantwortet, 12 Ein-Klick-Automatisierungen, What-if-Szenariosimulationen, ein Generator für Executive Briefings sowie MCP-Integrationen, über die der Assistent Live-Daten aus externen ERP-, CRM- und Jira-Systemen abrufen kann.

---

# 2. Fachbegriffe erklärt

| Begriff | Bedeutung |
|---|---|
| **S&OP** | Sales & Operations Planning — der monatliche, funktionsübergreifende Prozess, der einen gemeinsamen Bedarfsplan (den „Konsens") für die nächsten 12–24 Monate festlegt. |
| **S&OE** | Sales & Operations Execution — die tägliche/wöchentliche Ebene, die den Plan ausführt (Amazon-PO-Bestätigungen, Buy-Box-Überwachung, OTIF). |
| **Artikel vs. Produkt** | Ein *Produkt* ist eine physische SKU (3.560). Ein *Artikel* ist eine Produkt-×-Kanal-Kombination (9.173), z. B. „Standmixer auf Amazon". |
| **Cutoff** | Das Datum, an dem der monatliche Prognosezyklus eingefroren wird. Alles, was bis zum Cutoff bekannt ist, ist Input; alles danach ist Prognose. |
| **Horizont (h1, h2…)** | Prognosemonate im Voraus. h1 = nächster Monat, h12 = ein Jahr voraus. |
| **FPE / WAPE** | Forecast Percentage Error / Weighted Absolute Percentage Error — Genauigkeitskennzahlen. 98 % FPE bedeutet, dass die Prognose im Schnitt um ~98 % der tatsächlichen Nachfrage danebenliegt (typisch für langschwänzige, intermittierende Artikel). |
| **Bias** | Systematische Über- oder Unterprognose. Die alte Baseline überprognostiziert um +28,9 %; die ML-Prognose nur um +3,1 %. |
| **FVA** | Forecast Value Added — misst, ob ein Override des Planers die Prognose gegenüber der Maschine tatsächlich verbessert hat. Positives FVA = der Planer hat Wert hinzugefügt. |
| **Baseline** | Dannys statistische Altprognose (im Live-Zyklus: saisonale Naive + 12-Monats-Mittel als Proxy). |
| **ML-Prognose** | Die Machine-Learning-Prognose (vorberechnet pro Artikel, 12 Monate im Voraus). |
| **Reconciliation** | Abgleich, der sicherstellt, dass Artikelprognosen exakt auf Produktebene summieren (Hierarchie-Summierungsmatrix). |
| **Triage / Tiers** | Automatische Einordnung jedes Artikels in: **Autopilot** (ohne Eingriff), **Kollaborativ** (Maschine + Planer), **Planer-geführt** (Danny entscheidet). |
| **Ausnahmequeue** | Die kurze Liste der Artikel (25–100), bei denen ML und Baseline um mehr als einen £-Schwellenwert abweichen — die einzigen Artikel, die Danny prüfen muss. |
| **Value at Risk** | `|ML − Baseline| × Verkaufspreis` — die £-Auswirkung der Abweichung. |
| **Override** | Eine auditierte Änderung des Planers an einer Prognose, mit zwingendem Begründungscode. |
| **Konsens** | Der nach Overrides endgültig vereinbarte Plan — die Zahl, die an Supply, Finance und das ERP geht. |
| **OTIF** | On-Time In-Full — die B2B-Servicekennzahl (pünktlich und vollständig geliefert). |
| **OOS / Buy Box** | Out-of-Stock; die Amazon-Buy-Box ist der „In den Warenkorb"-Slot — verliert man sie, brechen die Verkäufe ein. |
| **Chargeback** | Amazons Strafe (3–9 % der COGS) für das Verpassen des 24-Stunden-PO-Bestätigungsfensters oder OTIF-Strafen. |
| **Konformes Band (P10/P90)** | Ein statistisch kalibrierter Unsicherheitsbereich um jede Prognose (80 % Zielabdeckung). |
| **Sicherheitsbestand** | Pufferbestand; das System berechnet ihn dynamisch aus Prognoseunsicherheit + Lieferzeit. |
| **NPI / EOL** | New Product Introduction / End of Life — Lebenszyklusphasen mit Sonderregeln. |
| **FVA-Scorecard** | Die Seite, die zeigt, ob Overrides die Genauigkeit verbessert haben, plus ausstehende Overrides, die auf Ist-Daten warten. |
| **Cycle Run** | Eine Ausführung der monatlichen Pipeline (Ingest → Forecast → Regeln → Triage → Explain), Knoten für Knoten nachverfolgt. |
| **MCP** | Model Context Protocol — ein Standard zur Anbindung des Assistenten an externe Systeme (ERP, CRM, Jira), sodass er deren Tools aufrufen kann. |
| **SSE** | Server-Sent Events — der Streaming-Kanal, über den der Co-Pilot Antworten tokenweise streamt. |
| **JWT / RBAC** | JSON Web Tokens für den Login; rollenbasierte Zugriffskontrolle (Planer / Admin / Auditor). |
| **Ledger** | Der nur-anfügbare, hash-verkettete Audit-Speicher für Overrides — Änderungen sind erkennbar. |

---

# 3. Anwendungsfälle und User Stories

## Danny — der Planer (Hauptnutzer)

- **„Als Danny möchte ich nur die Positionen sehen, die wichtig sind, damit ich nicht eine Woche damit verbringe, 9.173 Zeilen zu prüfen."**
  Die Workbench zeigt die Ausnahmequeue (nach Value at Risk sortiert), filtert nach Kanal/ABC/Risiko und bietet Ein-Klick-Detailpanels.
- **„Als Danny möchte ich wissen, warum die Maschine denkt, was sie denkt, damit ich ihr vertrauen oder sie hinterfragen kann."**
  Jeder Artikel hat eine Telemetriekarte (Historie, Amazon-Signale, Aktionsüberschneidungen, Lebenszyklusphase) und eine deterministische Erklärung; der Co-Pilot erklärt in verständlicher Sprache.
- **„Als Danny möchte ich, dass meine Änderungen protokolliert werden, damit später niemand den Plan anzweifeln kann."**
  Jeder Override erfordert einen Begründungscode und wird in den hash-verketteten Ledger geschrieben; das Audit-Badge zeigt die Anzahl.
- **„Als Danny möchte ich wissen, ob mein Urteil wirklich hilft, damit ich mich verbessern kann."**
  Die FVA-Scorecard zeigt Genauigkeitsdeltas pro Planer und pro Kategorie — sowie ausstehende Overrides, solange die Ist-Daten noch eintreffen.
- **„Als Danny möchte ich, dass meine täglichen Checks automatisch laufen, damit ich mich auf Entscheidungen konzentrieren kann."**
  Der Co-Pilot-Task-Hub führt 12 Automatisierungen aus: Amazon-PO-Bestätigungen, OTIF-Exposure, Buy-Box-Überwachung, Aktions-Plausibilität, Sicherheitsbestand, Value at Risk und mehr — jede liefert in unter 2 Sekunden eine Zusammenfassung in verständlicher Sprache.
- **„Als Danny möchte ich Fragen in einfachem Englisch stellen, damit ich nicht in Tabellen graben muss."**
  „Welche offenen Bestellungen gibt es in SAP?", „Welche Artikel sind von OOS bedroht?", „Erkläre die Ausnahmequeue" — beantwortet aus Live-Daten mit Quellen-Chips und Folgeempfehlungen.
- **„Als Danny möchte ich ‚What if' testen, bevor ich mich festlege, damit ich keinen schlechten Plan committe."**
  Szenariosimulation: Aktion verschieben, Komponente begrenzen, Preis ändern — Umsatz-, Fill-Rate- und Sicherheitsbestandsauswirkungen sofort sichtbar.
- **„Als Danny möchte ich den Plan ohne Tabellen-Neuaufbau an das Business übergeben."**
  Ein-Klick-Konsens-Commit, Executive-Briefing-PDF und (per MCP) das Pushen des Plans ins ERP.

## Admin

- **„Als Admin möchte ich verwalten, wer was darf, damit der Plan nicht versehentlich geändert werden kann."**
  RBAC: Der Planer kann prüfen/overriden, der Admin kann Nutzer und MCP-Server verwalten und fehlgeschlagene Zyklus-Knoten neu starten, der Auditor darf nur lesen.
- **„Als Admin möchte ich externe Systemverbindungen konfigurieren, damit der Co-Pilot Live-ERP/CRM/Jira-Daten nutzen kann."**
  Die MCP-Seite: Server hinzufügen, Verbindungen testen, Tools entdecken, Secrets maskieren und das Audit-Log jedes Tool-Aufrufs einsehen.

## Auditor / Finance

- **„Als Auditor möchte ich verifizieren, dass der Plan belastbar ist, damit die Zahlen einer Prüfung standhalten."**
  Nur-Lese-Zugriff auf den Ledger, das Audit-Log, Cycle Runs und die FVA-Scorecard; `/api/v1/audit/verify` erkennt jede Manipulation der Hash-Kette.

---

# 4. Wie das Projekt Dannys Arbeitslast reduziert — im Detail

Das System greift die **60–70 % der Planerarbeitszeit an, die für Datensuche, Abgleiche und Tabellenaufbereitung verloren gehen**. Konkret:

1. **Von 9.173 Zeilen zu ~67 Ausnahmen.** Die Triage-Engine leitet 84 % der Artikel an den Autopilot (ohne Eingriff) und lässt eine Queue von ~67–74 Positionen (etwa 21 % des Volumens) übrig, die wirklich einen Menschen brauchen. Danny prüft Ausnahmen, nicht den Katalog.
2. **Tägliche Checks laufen von selbst.** Amazon-PO-Bestätigungen (24-Stunden-Chargeback-Fenster), Buy-Box-/Glance-View-Überwachung, OTIF-Exposure — jede ist eine geplante Automatisierung, die als In-App-Benachrichtigung mit Deep-Link landet. Danny reagiert auf Alerts, statt Dashboards abzufragen.
3. **Antworten in Sekunden statt Queries.** Der Co-Pilot antwortet aus dem Live-Kontext (Queue, OTIF, Sicherheitsbestand, FVA, Zyklusstatus, externe MCP-Systeme) mit Quellen-Chips, damit Danny prüfen kann. Vorgeschlagene Folge-Chips halten das Gespräch in Gang.
4. **Ein-Klick-Task-Ausführung.** Jeder der 12 schnellen Tasks läuft in ≤1,4 s (nach Performance-Arbeit) und liefert eine Zusammenfassung in verständlicher Sprache plus Navigationsaktionen — z. B. öffnet „Exception queue review" die gefilterte Workbench-Queue.
5. **Entscheidungen werden auditiert und bewertet.** Jeder Override braucht einen Begründungscode und landet im hash-verketteten Ledger; FVA zeigt Danny, ob seine Overrides die Maschine schlagen (und um wie viel). Das macht Urteilsvermögen zu einer messbaren, verbesserbaren Fähigkeit.
6. **What-if ohne Excel.** Szenariosimulation und die Überführung eines Szenarios in auditierte Ledger-Entscheidungen vor dem Commit — Tage des Excel-Neubaus schrumpfen auf Minuten.
7. **Der Plan übergibt sich selbst.** Konsens-Commit, Executive-Briefing-PDF und ERP-Push (per MCP) ersetzen den monatlichen Tabellen-Marathon am Monatsende.
8. **Externe Systeme sind erreichbar.** MCP verbindet den Co-Pilot mit ERP (offene Bestellungen, Lagerbestände, Materialstamm), CRM (Konten, Opportunities, Fälle) und Jira (Issues) — jeder Tool-Aufruf wird auditiert, Schreib-Tools sind Admin-beschränkt.

---

# 5. Welche Tasks das Projekt ausführt und was sie liefern

## Der monatliche Zyklus (orchestrierte Pipeline, Knoten für Knoten)

| Knoten | Was er tut | Ausgabe |
|---|---|---|
| Ingest & Validierung | Lädt die 20 Tabellen, Schema-Checks, Data-Quality-Gates (z. B. fehlender Amazon-Februar-Feed) | Validierungsbericht in `cycle_runs` |
| Forecast | Lädt ML-Prognosen; statistischer Fallback für nicht abgedeckte/ausgelaufene Artikel | Prognose pro Artikel, 12 Monate, mit `source`-Tag |
| Reconcile | Summiert Artikelprognosen auf Produktebene (Hierarchie-Matrix) | Kohärenter Produkt- + Artikelplan |
| Regeln | EOL-Run-out-Degression, NPI-Run-Rate, Ghost-Promo, Amazon-Guard, Auftragsbuch-Untergrenze | Risiko-Flags pro Artikel |
| Triage | Value-at-Risk-Scoring, Tier-Routing (Autopilot/kollaborativ/planer-geführt) | Ausnahmequeue + Tiers |
| Explain | Deterministische Erklärungen (+ LLM, falls konfiguriert) | Artikel-Erklärungen, gecacht |
| Bänder & Sicherheitsbestand | Konforme P10/P90-Bänder, dynamischer Sicherheitsbestand | Band-Abdeckung, SS-Einheiten, Lagerhaltungskostenersparnis |
| Commit | Friert den Konsens nach Dannys Freigaben ein | Konsens-Export, Ledger-Einträge |

## Die 12 Co-Pilot-Schnelltasks (Task-Hub)

| Task | Rhythmus | Liefert |
|---|---|---|
| Amazon PO confirmations | S&OE täglich | Offene POs im 24-Stunden-Fenster, ATP-Defizite, gefährdete Chargeback-£ |
| OTIF exposure | S&OE täglich | B2B-Monate, die der Plan unterdeckt, gefährdete Straf-£ |
| Buy Box & glance-view watch | S&OE täglich | A-Artikel-ASINs mit >25 % Glance-Rückgang / OOS-Unterdrückung |
| Exception queue review | S&OP Wochen 1–2 | Größte Value-at-Risk-Ausnahmen, die eine Entscheidung brauchen |
| Data-quality gate | S&OP Wochen 1–2 | Schema-/Vollständigkeitswarnungen, die die Baseline ungültig machen könnten |
| Promo sanity check | S&OP Wochen 1–2 | Aktionen mit Uplift >3× historischem Durchschnitt |
| Safety stock & bands | S&OP Woche 3 | Dynamisch vs. statisch SS, Lagerhaltungskostenreduktion, Band-Abdeckung |
| Value at risk | S&OP Woche 3 | Geplanter vs. Baseline-£-Wert; P&L-relevante Ausnahmen |
| Scenario what-if | S&OP Woche 3 | Simulierte Aktions-/Begrenzungs-/Preisänderungen → Umsatz-, Fill-Rate-, SS-Deltas |
| Consensus commit readiness | S&OP Woche 4 | Offene vs. entschiedene Positionen; ist der Plan bereit zum Commit? |
| Executive briefing | S&OP Woche 4 | Monatliches Executive-S&OP-Briefing (PDF-Download) |
| FVA & autopilot | S&OP Woche 4 | FVA-Scorecard + Empfehlungen für risikolose ML-Übernahmen |

## Der Co-Pilot-Assistent

- Antwortet aus dem Live-Kontext mit **Quellen-Chips** (Projektdaten, Dokumente, MCP-externe Systeme, Allgemeinwissen — jeweils gekennzeichnet).
- Führt Tasks auf Anfrage aus („führe den Promo-Sanity-Check aus"), erklärt Tasks und schlägt Folgefragen vor.
- Fällt deterministisch zurück, wenn das LLM ausfällt — nie ein 500, nie eine erfundene Zahl (jede zitierte Zahl muss in den Belegen existieren).

## MCP-Aufrufe externer Systeme (Admin-konfiguriert)

- ERP: offene Bestellungen, Lagerbestände, Materialstamm, Lieferzeiten der Lieferanten.
- CRM: Konten, Opportunities, Fälle, Forecast-Commit.
- Jira: Issues suchen, Issue abrufen.
- Jeder Aufruf wird auditiert (wer, Tool, Argumente, Latenz, ok/Fehler); Schreib-Tools sind nur für Admins.

---

# 6. Geschäftsablauf — Von den Datenpunkten zur finalen Zusammenfassung (mit Dannys Freigaben)

```
DATENPUNKTE (20 Parquet-Tabellen, ~33,5 Mio. Zeilen)
  items · baseline_items · target_time_series · backtest/live_predictions
  orderbook · amazon_traffic/sell_out/inventory/forecast · promotions
  lifecycle · predecessors · covariates · error metrics
        │
        ▼
INGEST & VALIDIERUNG ──► Quality-Gates (fehlende Feeds, imputierte Anteile)
        │
        ▼
FORECAST (ML + statistischer Fallback) ──► RECONCILE auf Produktebene
        │
        ▼
GESCHÄFTSREGELN (EOL-Degression · NPI · Ghost-Promo · Amazon-Guard · Auftragsbuch-Untergrenze)
        │
        ▼
TRIAGE (Value-at-Risk-Scoring)
        ├── Autopilot-Tier  (~84 % der Artikel) ──► automatisch freigegeben, ohne Eingriff
        ├── Kollaborativer Tier ──► Maschine + Planer
        └── Planer-geführter Tier + AUSNAHMEQUEUE (67–74 Artikel)
                    │
                    ▼
        DANNY PRÜFT DIE QUEUE
        ├── ML-Prognose akzeptieren ─────────────────┐
        ├── Override mit Begründungscode ────────────┤ (hash-verketteter Ledger, auditiert)
        └── „Plan auf Auftragsbuch anheben" ─────────┤ (Ein-Klick, auditierte Aktion)
                    │                               │
                    ▼                               ▼
        FVA-BEWERTUNG (Hat Dannys Urteil Wert hinzugefügt?)
                    │
                    ▼
        KONSENS-COMMIT (Dannys Freigabe friert den Plan ein)
                    │
                    ├──► Konsens-Export (CSV) + ERP-Push (MCP)
                    ├──► Executive-Briefing-PDF (Waterfall: AOP vs. Baseline vs. Commercial vs. Konsens)
                    └──► Szenario-Promotion (What-if → auditierte Ledger-Entscheidungen)
                    │
                    ▼
        SUPPLY & FINANCE SETZEN UM (Sicherheitsbestand, OTIF, Amazon-POs)
```

**Dannys Freigabepunkte (alle auditiert):**
1. **Ausnahmeprüfung** — jede Position in der Queue akzeptieren oder overriden (Begründungscode Pflicht).
2. **Auftragsbuch-Untergrenze** — Ein-Klick „Plan auf Auftragsbuch anheben", wenn Buchungen den Plan übersteigen.
3. **Szenario-Promotion** — ein What-if-Szenario vor dem Commit in Ledger-Entscheidungen überführen.
4. **Konsens-Commit** — die finale Freigabe, die den Zyklus einfriert und Exporte/Briefing/ERP-Push auslöst.
5. **Autopilot-Übernahme** — FVA- & Autopilot-Empfehlungen, die Danny für risikoarme Artikel akzeptieren kann.

---

# 7. Welche Probleme das Projekt in Supply & Operations löst

## Supply-seitige Probleme

- **Stockouts (OOS) und Buy-Box-Verlust** — tägliche Amazon-Überwachung warnt bei >25 % Glance-View-Rückgängen und OOS-Unterdrückung, bevor Umsatz verloren geht.
- **Chargebacks (3–9 % der COGS)** — der 24-Stunden-Amazon-PO-Bestätigungs-Abgleicher markiert Defizite und empfiehlt Teilbestätigungen, bevor das Fenster schließt.
- **Überhöhter Sicherheitsbestand** — dynamischer Sicherheitsbestand (Band-Sigma × Lieferzeit) senkt die Lagerhaltungskosten bei A-Artikeln um ~69 % gegenüber der statischen Regel, bei ~97 % Service.
- **OTIF-Strafen** — B2B-Auftragsbuchmonate, die der Plan unterdeckt, werden mit den gefährdeten Straf-£ sichtbar gemacht.
- **Blindstellen im Auftragsbuch** — Buchungen, die am Cutoff bekannt sind, sind eine Untergrenze der Nachfrage; der Plan wird auf das Auftragsbuch angehoben, wenn Buchungen ihn übersteigen (im gemessenen Zyklus ~90.000 £/Monat wert).
- **EOL-Run-out** — ausgelaufene Artikel tragen weiterhin ~8 % des Volumens; eine kalibrierte Run-out-Degression ersetzt die alte „harte Null", die reale Verkäufe unterprognostizierte.

## Operations-/Planungsprobleme

- **Review-Überlastung** — 9.173 Artikel komprimiert auf eine ~67-Positionen-Ausnahmequeue (84 % ohne Eingriff).
- **Systematische Überprognose** — Bias der Alt-Baseline +28,9 % vs. ML +3,1 %; die Queue sortiert Abweichungen, nicht Fehler.
- **Fehlende/unvollständige Daten** — Data-Quality-Gates blockieren Zyklen bei fehlenden Feeds (z. B. Amazon-Februar-Ist-Daten), statt die Baseline stillschweigend zu korrumpieren.
- **Nicht verantwortbares Urteil** — jeder Override ist begründungscodiert, hash-verkettet und FVA-bewertet; Auditoren können die Kette verifizieren.
- **Tabellen-Übergaben** — Konsens-Export, Executive-Briefing-PDF und ERP-Push ersetzen manuelle Neuformatierung.
- **Blindheit gegenüber externen Systemen** — MCP verbindet den Co-Pilot mit ERP/CRM/Jira, sodass „Wie hoch ist der Lagerbestand?" aus dem Live-System beantwortet wird, nicht aus einem veralteten Export.

---

# 8. Tech-Stack-Zusammenfassung

| Ebene | Technologie |
|---|---|
| **Daten** | Parquet (~33,5 Mio. Zeilen), pandas + pyarrow, DuckDB (30-Mio.-Zeilen-Amazon-Tagestabellen per SQL-Pushdown), Polars |
| **Backend** | Python 3.9, FastAPI, Pydantic v2, LangGraph (Zyklus-Orchestrierung), scipy (Reconciliation/Optimierung) |
| **Persistenz** | MongoDB 7 (Produktion) mit lokalen JSON/SQLite-Fallbacks; nur-anfügbarer hash-verketteter Override-Ledger; SQLite FTS5 für die Dokumentsuche |
| **LLM** | Gemini (primär), GLM (Fallback), lokales flan-t5 (offline) über einen Failover-Client; deterministischer Template-Fallback immer |
| **Auth** | JWT + RBAC (Planer / Admin / Auditor) |
| **Frontend** | React 18, Vite, Tailwind CSS, Vitest + React Testing Library |
| **MCP** | JSON-RPC 2.0 über streamable HTTP/SSE; Demo-ERP/CRM/Jira-Server; maskierte Secrets; vollständiges Call-Audit |
| **Ops** | Scheduler (täglich/monatlich, UK-Zeit), In-App-Benachrichtigungen, Admin-Neustart fehlgeschlagener Knoten, lastgetestete API (P95 < 400 ms bei 10 Nutzern) |
| **Testing** | 194+ Backend-Tests, 25 Frontend-Tests; Produktions-Build grün |

---

# 9. Dannys geführter Rundgang — Arbeiten mit der Automatisierung

Dies ist die konkrete „Was klicke ich, in welcher Reihenfolge"-Tour durch die Website, abgestimmt auf den S&OP-Rhythmus. Die Automatisierung übernimmt die schwere Arbeit; Dannys Aufgabe ist **prüfen, entscheiden, freigeben** — niemals Datensuche.

## 9.1 Jeden Morgen — die tägliche S&OE-Schleife (10 Minuten)

1. **Als `danny` anmelden** (Planer-Rolle) auf dem Login-Bildschirm.
2. **Die Benachrichtigungsglocke prüfen** (oben rechts im Header). Ein rotes Badge mit Zahl bedeutet, dass die Automatisierungen über Nacht etwas gefunden haben (Amazon-PO-Fenster schließt, Buy-Box-Alert, OTIF-Exposure, ein Zyklus-Knoten ist fertig). Klicken Sie eine Benachrichtigung an — sie verlinkt direkt auf die relevante Seite (Operations-Run-Detail, Workbench-Artikel usw.).
3. **Die Co-Pilot-Seite öffnen** (Navigationsmenü). Das ist der tägliche Task-Hub.
4. **Die Schnelltasks filtern**: Rhythmus = *S&OE täglich*, Kanal = *Amazon* (oder B2B). Das Raster rendert exakt die Tasks für diesen Rhythmus.
5. **Die täglichen Tasks ausführen** — klicken Sie **Run** auf:
   - *Amazon PO confirmations* — der Abgleicher prüft jede offene Bestellung gegen Available-to-Promise; wenn eine Position nicht vollständig bestätigt werden kann, empfiehlt er eine Teilbestätigung **bevor das 24-Stunden-Chargeback-Fenster schließt**.
   - *Buy Box & glance-view watch* — warnt bei A-Artikel-ASINs mit >25 % Glance-View-Rückgang über 3 Tage oder OOS-Unterdrückung.
   - *OTIF exposure* — B2B-Auftragsbuchmonate, die der Konsensplan unterdeckt, mit den gefährdeten Straf-£.
6. **Das Ergebnis lesen**: jeder Run postet eine Zusammenfassung in verständlicher Sprache als Assistenten-Nachricht in den Chat, mit **Action-Chips** darunter (z. B. „Artikel 103125 prüfen", „OTIF-Queue öffnen"). Klicken Sie einen Chip, um zur gefilterten Ansicht zu springen.
7. **Nur auf Dringendes reagieren**: die Task-Karten zeigen ein pulsierendes „N dringend"-Badge, wenn die Automatisierung etwas gefunden hat, das Danny heute braucht. Alles andere ist bereits erledigt.

**Was Danny NICHT tut:** Dashboards abfragen, Tabellen abgleichen oder Amazon-Feeds manuell prüfen. Die Automatisierungen laufen nach Plan und *pushen* die Ergebnisse zu ihm.

## 9.2 Wochen 1–2 — S&OP-Vorbereitung (die Ausnahmeprüfung)

1. **Co-Pilot → zuerst *Data-quality gate* ausführen.** Wenn der Zyklus Warnungen hat (z. B. einen imputierten Amazon-Feed), zeigt das QualityBanner oben in der Workbench sie; ein blockierter Zyklus ist besser als eine stillschweigend korrumpierte Baseline.
2. **Co-Pilot → *Exception queue review* ausführen.** Der Ergebnis-Chip öffnet die **Workbench** mit der Queue, vorgefiltert auf die Positionen, die noch eine Entscheidung brauchen.
3. **Workbench — die Entscheidungsschleife** (pro Ausnahme wiederholen):
   - Die **Ausnahmetabelle** zeigt Value at Risk (£), Baseline vs. ML vs. aktueller Endstand, nach Auswirkung sortiert. Nutzen Sie die Filter (Kanal / ABC / Risikotyp), um sich zu fokussieren.
   - Klicken Sie einen Artikel → das **DetailPanel** öffnet sich rechts:
     - Lesen Sie die **Erklärung** (warum dieser Artikel markiert ist).
     - Klicken Sie **Explain with AI** für eine verständliche Erläuterung der Signale.
     - Prüfen Sie die Telemetrie: Historie-Diagramm mit P10/P90-Band, Amazon-Signale (Glance MoM, p70, OOS), Auftragsbuch-Phasierung, Lebenszyklusphase.
     - Entscheiden Sie:
       - **ML-Prognose akzeptieren** — keine Aktion nötig; der Artikel bleibt wie er ist.
       - **Override** — Endmenge eingeben, **Begründungscode** wählen (Pflicht), absenden. Das schreibt einen auditierten, hash-verketteten Ledger-Eintrag mit Ihrem Namen und der £-Auswirkung.
       - **Plan auf Auftragsbuch anheben** — ein Klick, wenn Buchungen am Cutoff den Plan übersteigen (die Automatisierung setzt das Flag `ORDER_BOOK_EXCEEDS_PLAN`); auditiert mit Begründung „Order Book Floor Applied".
   - Das **Audit-Badge** im Panel zeigt, dass Ihre Entscheidung protokolliert ist.
4. **Co-Pilot → *Promo sanity check* ausführen** — Aktionen mit Uplift >3× dem historischen Durchschnitt werden markiert und brauchen eine KAM-Begründung, bevor sie in die Baseline einfließen.

**Was Danny NICHT tut:** 9.173 Artikel prüfen. Die Triage hat ~84 % der Artikel bereits an den Autopilot geleitet; nur die ~67–74 Ausnahmen erreichen diesen Bildschirm.

## 9.3 Woche 3 — Finanzialisierung, Sicherheitsbestand und What-if

1. **Co-Pilot → *Value at risk* ausführen** — die £-Sicht von Plan vs. Baseline; die Positionen, die das P&L bewegen.
2. **Co-Pilot → *Safety stock & bands* ausführen** — dynamisch vs. statisch Sicherheitsbestand, Lagerhaltungskostenreduktion (~69 % bei A-Artikeln) und Band-Abdeckung. Gehen Sie für die vollständigen Tabellen in **Supply & Risk** (Band-Kalibrierungshistorie, Lieferzeiten, OTIF nach Kunde).
3. **Szenario-Seite** (Navigationsmenü) — die What-if-Sandbox:
   - **Generieren** Sie ein Szenario aus einem Prompt (z. B. „15 % B2B-Upside für margenstarke Produkte nächsten Monat").
   - **Vergleichen Sie bis zu 3 Versionen** nebeneinander (Umsatz, Marge, OTIF-Exposure, Sicherheitsbestands-Deltas).
   - Wenn eine gewinnt, klicken Sie **Promote to ledger** — die Mengen des Monats 1 des Szenarios werden zu auditierten Overrides (Begründung „Commercial Intelligence (Account Feedback)") **vor** dem Commit. Nichts wird stillschweigend übernommen.

## 9.4 Woche 4 — Commit und Übergabe

1. **Co-Pilot → *Consensus commit readiness* ausführen** — offene vs. entschiedene Positionen; die Automatisierung sagt Ihnen, ob der Plan bereit ist.
2. **Co-Pilot → *FVA & autopilot* ausführen** — die Scorecard plus Empfehlungen für risikolose ML-Übernahmen. Akzeptieren Sie die, denen Sie zustimmen; jede Übernahme wird auditiert.
3. **Den Konsens committen** — die Schaltfläche **Commit consensus** im Header (nur aktiv, wenn jede Ausnahme entschieden ist). Das friert den Zyklus ein und löst die Übergaben aus.
4. **Übergabe**:
   - **Export draft plan** (Header) — die Konsens-CSV.
   - **Executive briefing** (Header) — das monatliche PDF für die Führungsebene.
   - **MCP-ERP-Push** (Admin) — der freigegebene Plan wird an den ERP-Adapter gepusht; Sicherheitsbestand und Bestellpunkte werden aus der P10–P90-Spanne neu berechnet.
5. **FVA- & Audit-Seite** — nachdem die Ist-Daten eintreffen, zeigt die Scorecard, ob Dannys Overrides Wert hinzugefügt haben (pro Planer, pro Kategorie). Das ist die Feedback-Schleife, die sein Urteil messbar macht.

## 9.5 Den Co-Pilot fragen (jederzeit)

- Geben Sie eine Frage ein: *„Welche offenen Bestellungen gibt es in SAP?"*, *„Welche drei Ausnahmen tragen den größten Value at Risk?"*, *„Wie groß ist das OTIF-Exposure?"*, *„Erkläre den Promo-Sanity-Check"*.
- Die Antwort streamt mit einem **Quellen-Chip** (Projektdaten / Dokument / MCP-externes System / Allgemeinwissen), damit Sie wissen, worauf sie sich stützt, plus **Folgevorschlags-Chips** zum Weitermachen.
- Jede zitierte Zahl wird gegen die Belege geprüft; fällt das LLM aus, übernimmt eine deterministische Antwort — das System erfindet nie Zahlen.

**Die Arbeitsteilung in einem Satz:** die Automatisierung *findet, berechnet, warnt und entwirft*; Danny *prüft, entscheidet und gibt frei* — und jede Entscheidung wird protokolliert und bewertet.

---

# 10. Die Demo für Danny präsentieren — Leitfaden für den Präsentierenden

## 10.1 Setup-Checkliste (bevor Danny kommt)

- [ ] Backend läuft: `python -m uvicorn server:app --host 127.0.0.1 --port 8000` (aus `backend/`)
- [ ] Frontend läuft: `npm run dev` (aus `frontend/`)
- [ ] Demo-MCP-Server laufen: `python -m app.mcp.demo --kind erp --port 9101` (und crm 9102, jira 9103, falls Sie sie zeigen)
- [ ] MCP-Server in der UI konfiguriert (Admin), der auf `http://127.0.0.1:9101/` zeigt — Verbindung testen, damit entdeckte Tools sichtbar sind
- [ ] Ein monatlicher Zyklus wurde ausgeführt (Operations-Seite zeigt einen Run mit allen grünen Knoten), damit Workbench, Supply & Risk und FVA-Seite echte Daten haben
- [ ] FVA-Demozeilen werden beim Backend-Start automatisch angelegt (`app/seed_demo_fva.py`): historische Overrides für Monate mit Ist-Daten, damit die FVA-Scorecard ausgereifte, bewertete Zeilen zeigt. Zurücksetzen/auffrischen mit `python -m app.seed_demo_fva --reset` (aus `backend/`)
- [ ] Demo-Konten bereit: `danny` (Planer), `admin`, `auditor` — Passwörter aus der lokalen `.env` (`SEED_PLANNER_PASSWORD` usw.)
- [ ] Browser auf der Login-Seite geöffnet; andere Tabs geschlossen

## 10.2 Das 25-Minuten-Skript

**Szene 1 — Das Problem (2 Min.).** Öffnen Sie die Workbench. Zeigen Sie die Zusammenfassungskarten: 9.173 Artikel im Katalog vs. ~67 Ausnahmen in der Queue. Sagen Sie: *„Heute prüfen Sie 9.173 Zeilen. Mit diesem System prüfen Sie die ~67 Positionen, bei denen Maschine und Baseline um echtes Geld abweichen — und alles, was Sie entscheiden, wird auditiert."*

**Szene 2 — Die tägliche Schleife (5 Min.).** Gehen Sie zu Co-Pilot. Zeigen Sie die Benachrichtigungsglocke (einen echten Alert, falls einer existiert), dann den Task-Hub: Rhythmus = S&OE täglich, *Amazon PO confirmations* ausführen. Die Antwort landet mit Action-Chips im Chat. Sagen Sie: *„Das ist Ihr 10-Minuten-Morgen: Die Automatisierung hat jede offene Bestellung gegen das 24-Stunden-Chargeback-Fenster geprüft und sagt Ihnen genau, welche Positionen eine Entscheidung brauchen — mit den gefährdeten £."* Klicken Sie einen Chip, um den Deep-Link zu zeigen.

**Szene 3 — Die Ausnahmeprüfung (6 Min.).** Führen Sie *Exception queue review* aus dem Task-Hub aus → sie öffnet die Workbench-Queue. Öffnen Sie eine Position im DetailPanel: Lesen Sie die Erklärung, klicken Sie **Explain with AI**, zeigen Sie das Band-Diagramm und die Amazon-Signale. Dann **overriden** Sie mit einem Begründungscode und zeigen Sie, wie das Audit-Badge erscheint. Sagen Sie: *„Jeder Override ist hash-verkettet — ein Auditor kann verifizieren, dass niemand ihn danach verändert hat. Und in einem Monat sagt Ihnen FVA, ob Ihr Urteil die Maschine geschlagen hat."*

**Szene 4 — Das What-if (4 Min.).** Öffnen Sie Szenarien. Generieren Sie eines aus einem Prompt, vergleichen Sie 2–3 Versionen, dann **Promote to ledger**. Sagen Sie: *„Was früher Tage des Excel-Neubaus kostete, dauert jetzt Minuten — und das übernommene Szenario landet als auditierte Entscheidungen, nicht in einer Neben-Tabelle."*

**Szene 5 — Die Übergabe (4 Min.).** Zeigen Sie **Commit consensus** (Header), dann **Export draft plan** und **Executive briefing**. Sagen Sie: *„Der Plan übergibt sich selbst: CSV für Supply, PDF für die Führungsebene und der ERP-Push per MCP."*

**Szene 6 — Externe Systeme (4 Min.).** Öffnen Sie als `admin` **MCP**. Zeigen Sie den konfigurierten ERP-Server, testen Sie die Verbindung, listen Sie die entdeckten Tools und zeigen Sie das Audit-Log echter Aufrufe. Dann zurück zu Co-Pilot als `danny`: Fragen Sie *„Welche offenen Bestellungen gibt es in SAP?"* — die Antwort trägt den smaragdgrünen Quellen-Chip `mcp:erp_...`. Sagen Sie: *„Der Co-Pilot liest Ihr Live-ERP, CRM und Jira — und jeder Aufruf wird auditiert; Schreib-Tools sind nur für Admins."*

## 10.3 Kernbotschaften

- **Ausnahmen, nicht Zeilen** — 9.173 Artikel → ~67 Entscheidungen; 84 % der Artikel laufen ohne Eingriff.
- **Die Maschine ist ehrlich** — die ML-Prognose schlägt die Alt-Baseline beim Bias (+3,1 % vs. +28,9 %) und bei der Genauigkeit auf Produktebene; die Queue sortiert Abweichungen, nicht Fehler.
- **Urteil wird messbar** — begründungscodierte Overrides, hash-verketteter Ledger, FVA-Scorecard. Dannys Erfahrung ist jetzt ein Vermögenswert, den das System *bewertet*, keine Blackbox.
- **Der tägliche Trott ist automatisiert** — PO-Bestätigungen, Buy Box, OTIF laufen von selbst und pushen Alerts zu ihm.
- **Nichts ist still** — Data-Quality-Gates blockieren schlechte Zyklen; das LLM erfindet nie Zahlen; jeder externe Aufruf wird auditiert.

## 10.4 Zu vermeidende Fallstricke

- **Zitieren Sie nicht die Headline-Zahlen des Briefs** (98 % → 41 % MinT, £25,49 D2C-Marge) — die gemessenen Werte weichen ab (siehe `DEVELOPMENT_PLAN.md` Abschnitt 3). Nutzen Sie die gemessene Story: ~6 Punkte produktbezogenes FVA, Bias +3,1 % vs. +28,9 %, ~69 % Lagerhaltungskostenreduktion.
- **Demonstrieren Sie keine LLM-abhängigen Funktionen ohne Netz/Quota** — wenn Gemini/GLM ausfallen, antwortet der deterministische Fallback trotzdem; sagen Sie das explizit („das System degradiert elegant"), statt es zu verstecken.
- **Überspringen Sie den Demo-ERP-Server nicht** — die MCP-Szene scheitert ohne ihn. Prüfen Sie, dass `9101` läuft und die Server-Konfiguration existiert, bevor Danny kommt.
- **Zeigen Sie die Auditor-Rolle nicht in Danny-zentrierten Abläufen** — nutzen Sie `danny` für die Workbench und `admin` nur für MCP-/Nutzerverwaltung.
- **Führen Sie während der Demo keinen frischen Monatszyklus aus** — er dauert Minuten; nutzen Sie den vorab gelaufenen Zyklus, damit jede Seite gefüllt ist.
- **Behaupten Sie nicht, FVA werde täglich bewertet** — Ist-Daten sind monatlich, ein März-Override kann also erst bewertet werden, wenn die März-Ist-Daten eintreffen. Der Demo-Seed löst das, indem er *historische* Overrides (im Ledger mit `demo-seed` getaggt) für Monate hinzufügt, die bereits Ist-Daten haben: Die Scorecard zeigt echte bewertete Zeilen (z. B. FVA ≈ +2,9 Punkte, FPE Planer 3,1 % vs. ML 6,0 % vs. Baseline 24,0 %) plus die Tabelle der ausstehenden Entscheidungen des aktuellen Zyklus. Rahmen Sie es als *„die Engine bewertet jeden Override in dem Moment, in dem sein Monat schließt — hier sind die bewerteten Entscheidungen des letzten Quartals, und diese warten auf die März-Ist-Daten"*.

---

# 11. Wie das System rechnet — Agents, Modelle und die deterministische Formulierung

## Das Grundprinzip: zwei getrennte Ebenen

Das System ist **kein** „KI-Modell, das Prognosen berechnet". Es hat zwei streng getrennte Ebenen:

1. **Deterministische Rechen-Engine** — jede Zahl (Prognose, Methodenwahl, Reconciliation, Risiko-Flags, FVA, Task-Ergebnisse) wird durch explizite Formeln und Regeln in Python berechnet. Kein LLM ist beteiligt.
2. **Optionale LLM-Ebene** — erzeugt nur *Text*: die „Inform"-Zusammenfassung im agentischen Forecast und die Chat-Antworten des Co-Piloten. Sie berechnet nie eine Zahl; Guardrails verwerfen jeden Text, der Zahlen enthält, die nicht in den Belegen vorkommen.

## 11.1 Der Forecasting-„Agent" — 4 Stufen, 3 deterministisch + 1 optional mit LLM

`backend/app/forecasting/agent.py` definiert den agentischen Forecast als **Research → Process → Forecast → Inform**:

- **Research** — deterministische Merkmalsextraktion: Länge der Historie, Anteil Nicht-Null-Monate, Mittel der letzten 3 vs. der vorherigen 3 Monate, Trend in %, im Auftragsbuch gebuchte/übernommene Mengen für Monat 1, Lebenszyklusphase, Monate seit Launch / bis EOL, Kanal, ABC-Klasse, Aktionsüberschneidungen.
- **Forecast** — deterministische 12-Monats-Prognose (Formeln unten).
- **Process** — Holdout-Bewertung aller Methoden + Reconciliation auf Produktebene.
- **Inform** — eine deterministische Zusammenfassung wird immer erstellt; das LLM darf sie *umformulieren*, aber nur, wenn `validate_narrative` bestätigt, dass jede Zahl im erzeugten Text im JSON-Payload existiert. Sonst gilt die deterministische Fassung.

## 11.2 Welche Prognosemodelle — exakte Formeln

**Vorberechnetes ML** (Artefakte `live_predictions` / `backtest_predictions`): wird geladen, nicht in diesem Projekt trainiert. Die Laufzeit-Engine ergänzt eine **deterministische statistische Schicht** (`stat_methods.py`) mit diesen Formeln (Historie als Matrix H, Spalten = Monate, NaN vor der ersten Beobachtung):

| Methode | Formel |
|---|---|
| `ma_k` | `Mittelwert(H[letzte k Monate])`, konstant für h1–h12 |
| `median6` | `Median(H[letzte 6 Monate])` |
| `seasonal_naive` | Wert desselben Kalendermonats ein Jahr zuvor; 6-Monats-MA, falls nicht beobachtet |
| `ses` (α=0,3) | `level_t = level_{t-1} + 0,3·(x_t − level_{t-1})` |
| `croston_sba` (α=0,1) | geglättete Bedarfsgröße `z`, geglättetes Intervall `p`; **Rate = (1 − α/2)·z/p** (Syntetos–Boylan-Bias-Korrektur) |
| `combo_sn_ma12` | `Mittelwert(seasonal_naive, ma12)` pro Monat |
| EOL-Degression (`fallback.py`) | `Basis = ma3`, dann `Prognose_h = Basis · λ^h`; λ = `exp(Steigung von log(ratio_k) über k=0..12)`, wobei `ratio_k` = Verkäufe k Monate nach EOL ÷ 3-Monats-Mittel vor EOL |

**Die Methodenwahl ist ein deterministischer Entscheidungsbaum** (`dynamic.py`):

```
Anteil Nicht-Null < 0,35     → croston_sba   (intermittierende Nachfrage)
Phase == POST_EOL            → ma3           (Run-out-Degression)
≥ 24 beobachtete Monate      → combo_sn_ma12 (Saisonalität + Niveau)
sonst                        → ma6           (kurz, stabil)
```

**Holdout-Validierung** (`agent.py`): die letzten 3 beobachteten Monate werden zurückgehalten; jede Methode wird mit **WAPE = Σ|Ist − Prognose| / Σ|Ist|** bewertet. Das Ranking wird dem Planer angezeigt („warum diese Methode"), die Auswahl selbst trifft aber der Entscheidungsbaum oben.

## 11.3 Reconciliation und Bänder

- **Produkt-Reconciliation** (`dynamic.py`): Artikelprognosen werden pro Produkt und Monat/Kanal summiert (`groupby("month").sum()`), sodass die Produktsumme exakt der Summe seiner Artikel entspricht.
- **P10/P90-Bänder**: werden offline in den Artefakten vorberechnet (konforme Kalibrierung) und vom Service geladen (`service.py`) — zur Laufzeit nicht neu berechnet.

## 11.4 Triage und FVA — die Geschäftsregel-Formeln

- **Value at Risk** (vorberechnet in `triage_live.parquet`): `|ML − Baseline| × Verkaufspreis` → sortiert die Ausnahmequeue.
- **FVA** (`evaluation/fva.py`): für Overrides, deren Zielmonat Ist-Daten hat:
  - `FPE = Σ|Prognose − Ist| / Σ(Ist) × 100`
  - **`FVA_Punkte = FPE(ML) − FPE(Planer)`** — positiv = der Planer hat Wert hinzugefügt. Formale ML-Übernahmen werden gezählt, aber vom Fehlervergleich ausgeschlossen; pro Artikel-Monat gewinnt der letzte Ledger-Eintrag.

## 11.5 Die LLM-Ebene — Modelle und Failover

`narrative/llm.py` baut Clients in Prioritätsreihenfolge: **Emergent-Universalschlüssel (Standard: Gemini 3 Flash Preview) → direktes Gemini → GLM → lokales Hugging-Face-Modell (z. B. flan-t5-base)**. Mehrere konfigurierte Provider werden in einen `FailoverClient` gewickelt, der sie der Reihe nach versucht, die Provider-Gesundheit protokolliert und bei Quoten-/Fehlern zum nächsten wechselt. Ist keiner konfiguriert, läuft die App **rein deterministisch**.

## 11.6 Wie der deterministische Chat-Fallback formuliert ist

`api/extended.py`:

- `_structured_intent(question)` ordnet die Frage einem bekannten Zweig zu (z. B. `fva`, `exceptions`, `cycle`, `task`) mit denselben Präzisionsregeln wie der Retrieval-Router.
- `deterministic_answer(question, ctx)` formatiert den Zweig **nur aus dem Live-Kontext** — z. B. liefert der FVA-Zweig `"FVA: {n} ausgereifte Overrides; Planer-Overrides liegen bei {pts:+.1f} Punkten gegenüber der ML-Baseline…"`.
- `_deterministic_or_honest`: passt kein Zweig, kommt eine **ehrliche** Antwort „Die Projektdaten enthalten diese Antwort nicht" — nie eine per Stichwort geratene Zahl.
- Allgemeinwissensfragen fallen auf einen kuratierten statischen Konzept-KB zurück (`narrative/concepts.py`).
- MCP-Fehler liefern eine deterministische „Verbindung nicht verfügbar"-Antwort.
- LLM-Antworten zu Projektdaten werden mit `_unverifiable_numbers` gegen die Zyklusdaten geprüft; jede nicht verifizierbare Zahl wird in der Antwort markiert.

## 11.7 Die 12 Task-Hub-Automatisierungen

Jeder Task (`api/tasks.py`) ist eine **kleine deterministische Analyse über die Live-Zyklusdaten** — PO-Bestätigungen, OTIF-Exposure, Buy-Box-Überwachung usw. — und liefert eine in verständlicher Sprache formulierte Zusammenfassung, die aus echten Werten gebaut wird, nicht aus LLM-Prosa.

**Zusammenfassung in einem Satz:** *Die Zahlen stammen aus deterministischen Formeln und Entscheidungsbäumen (statistische Methoden, Holdout-WAPE, Value-at-Risk-Triage, FVA); die ML-Prognosen sind vorberechnete Inputs; das LLM (Gemini → GLM → lokales flan-t5, mit Failover) schreibt nur Text, und jede Zahl, die es schreibt, wird gegen die Belege geprüft — sonst gilt der deterministische Text.*

---

*Begleitdokumente: `DEVELOPMENT_PLAN.md` (Architektur, Daten-Audit, Erkenntnisse), `ENHANCEMENT_PLAN.md` (Co-Pilot, Task-Hub, Automatisierungen, MCP), `backend/app/mcp/README.md` (MCP-Integrationsleitfaden).*
