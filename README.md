# Flint

<img src="assets/brand/flint-horizontal.png" width="760" alt="Flint">

**Il contesto giusto. La versione giusta. Le decisioni che restano.**

Flint è un toolkit locale per agenti AI che lavorano su codice e documenti.
Permette di leggere soltanto il contenuto necessario, applicare modifiche alla
versione effettivamente letta e recuperare decisioni dopo un cambio di contesto.

Funziona con **Python 3.11+ su Linux e WSL**, usando la libreria standard.
Il motore non richiede un modello, un server o un servizio esterno.

## Cosa puoi fare

| Esigenza | Flint |
|---|---|
| Leggere una parte del codice | Seleziona definizioni Python complete, intervalli o corrispondenze testuali. |
| Modificare il file corretto | Collega ogni proposta a un ticket di revisione; rifiuta una base diventata obsoleta. |
| Riprendere un lavoro | Salva decisioni in memoria persistente, con ricerca e storico delle versioni. |
| Riconoscere fonti cambiate | Le memorie collegate a un file possono essere escluse dal richiamo quando i byte cambiano. |
| Rileggere file già conosciuti | Restituisce differenze rispetto a una versione completa precedentemente confermata. |
| Recuperare un originale | Conserva snapshot esatti e riferimenti locali recuperabili. |
| Preparare il contesto | Assembla fonti e dipendenze dichiarate entro un budget esplicito. |

## Inizia subito

Dalla cartella del progetto:

```bash
python3 -S dist/flint.pyz --help
python3 -S dist/flint.pyz --workspace . work open examples/hello.py
```

Il pacchetto [flint.pyz](dist/flint.pyz) contiene il motore e le sue risorse.
Puoi copiarlo vicino al progetto su cui lavora il tuo agente. Per usare i
sorgenti direttamente è disponibile anche `python3 bin/flint`.

## Leggi, modifica, recupera

`work open` restituisce il sorgente e un ticket, per esempio `W1 e1 source`.
Il ticket identifica il file e la sua revisione nel workspace corrente.

Prepara un piano che sostituisca la seconda riga dell'esempio:

```json
{"edits":[[2,1,"    return f'Ciao, {name}!'\n"]]}
```

Salvalo come `edit.json` e usa il riferimento restituito dall'apertura:

```bash
python3 -S dist/flint.pyz --workspace . work edit e1 edit.json
python3 -S dist/flint.pyz --workspace . work show e1
```

`e1` è illustrativo: usa sempre il ticket della tua chiamata. `work show`
recupera il contenuto associato al ticket; una modifica riuscita restituisce
un nuovo riferimento. Se il file è cambiato nel frattempo, Flint rifiuta la
scrittura. Una proposta Python con sintassi errata può essere conservata
come candidato da correggere, mantenendo intatto il file originale.

Per un agente che invia JSON:

```bash
printf '%s\n' '{"op":"open","path":"examples/hello.py"}' |
  python3 -S dist/flint.pyz --workspace . work call
```

[Protocollo JSON e ticket](integrations/skills/flint-context/references/work-tickets.md).

## Memoria che segue il lavoro

Conserva le decisioni e richiamale in una sessione successiva:

```bash
python3 -S dist/flint.pyz --workspace . memory remember examples/decisions.txt --key project-decisions
python3 -S dist/flint.pyz --workspace . memory recall 'project decisions' --budget 8192 --compact
python3 -S dist/flint.pyz --workspace . memory history project-decisions
```

Una nuova registrazione della stessa chiave sostituisce la versione corrente,
conservando lo storico. `memory recover RIFERIMENTO` recupera il testo originale.
La ricerca è lessicale e restituisce memorie complete entro il budget.

Per una memoria che deve essere valida soltanto finché il file rimane identico:

```bash
python3 -S dist/flint.pyz --workspace . memory remember examples/decisions.txt --key tracked-decisions --track-source
```

Quando il file cambia o scompare, quella versione viene esclusa dai richiami
correnti e segnalata come obsoleta. Per conoscere il nuovo contenuto serve
leggere la fonte e aggiornare esplicitamente la memoria.

[Memorie, versioni e sessioni](integrations/skills/flint-context/references/memory-wire.md).

## Contesto mirato

```bash
python3 -S dist/flint.pyz --workspace . map examples/hello.py
python3 -S dist/flint.pyz --workspace . inspect examples/hello.py greet --editable
```

Le selezioni mantengono il codice esatto. I budget sono espressi in byte UTF-8;
un budget insufficiente viene segnalato, senza presentare un frammento come
contenuto completo. Dopo una perdita di contesto, avvia una nuova sessione
e recupera le fonti necessarie prima di modificarle.

## Più veloce nella versione 0.18

Il percorso JSON carica l'editor soltanto quando serve e riduce il lavoro
iniziale della CLI, mantenendo invariati contenuto e controlli di revisione.

| Operazione JSON sullo stesso file Python | 0.17 | 0.18 | Tempo in meno |
|---|---:|---:|---:|
| Aprire un file | 62,12 ms | **45,64 ms** | **26,5%** |
| Recuperare il contenuto di un ticket | 66,97 ms | **48,65 ms** | **27,4%** |
| Applicare una modifica | 103,58 ms | **84,95 ms** | **18,0%** |

Mediane locali di 12 esecuzioni per versione e operazione, sullo stesso sistema
Linux e con lo stesso interprete Python. Su tre input le riduzioni osservate
sono del **26–30% per lettura e recupero JSON** e del **17–22% per le modifiche**.
Sono tempi degli strumenti; prestazioni complessive e consumo di token
dipendono dal lavoro e dall'integrazione dell'agente.

## Integrazione

Flint è esplicito: l'agente sceglie quando richiamarlo. La
[skill Flint Context](integrations/skills/flint-context/SKILL.md) descrive
letture, modifiche, memoria e recupero. I protocolli JSON permettono di
integrarlo anche senza installare la skill.

Lo stato rimane nella cartella `.flint/` del workspace, esclusa da Git.
I riferimenti sono locali a quel workspace. I codec opzionali per JSON e testo
ripetuto e il motore C COPY/1 sono descritti nelle
[operazioni avanzate](integrations/skills/flint-context/references/advanced-context.md).

## Sorgenti e distribuzione

- [Motore Python](flint/), [CLI](bin/flint) e [pacchetto pronto](dist/flint.pyz).
- [Manifest con gli hash dei sorgenti](dist/manifest.json).
- [Motore C opzionale](native/flint_copy.c).
- [Logo, font e varianti del marchio](assets/preview.html).

Per ricostruire il pacchetto dai sorgenti, senza scaricare dipendenze:

```bash
python3 tools/package.py
```

Le attribuzioni e le licenze dei componenti adattati sono conservate in
`vendor/` e nel pacchetto. Il font Micro 5 include la propria
[licenza OFL](assets/fonts/OFL.txt).
