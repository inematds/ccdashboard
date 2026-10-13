#!/usr/bin/env python3
"""Manutenção do banco da telemetria: resumo diário, limpeza e checkpoint do WAL.

Roda de hora em hora (timer systemd --user). Recalcula o resumo de ontem e
de hoje (dias inteiros), apaga eventos brutos com mais de RETENCAO_DIAS e pendências órfãs
com mais de 24 h, e encolhe o WAL.
"""
import os
import sqlite3
import sys
import time

DB = os.environ.get(
    "TELEMETRIA_DB",
    os.path.expanduser("~/.local/share/telemetria-agentes/eventos.db"),
)
RETENCAO_DIAS = int(os.environ.get("TELEMETRIA_RETENCAO_DIAS", "30"))


def main():
    if not os.path.exists(DB):
        print("sem banco ainda:", DB)
        return 0
    con = sqlite3.connect(DB, timeout=10, isolation_level=None)
    agora = time.time()
    con.execute("BEGIN IMMEDIATE")
    con.execute(
        "INSERT OR REPLACE INTO resumo_diario (dia, projeto, ferramenta, chamadas, falhas, duracao_total_ms)"
        " SELECT date(ts,'unixepoch','localtime'), COALESCE(projeto,'?'), ferramenta,"
        " COUNT(*), SUM(status='erro'), COALESCE(SUM(duracao_ms),0)"
        " FROM eventos WHERE evento IN ('PostToolUse','PostToolUseFailure') AND ferramenta IS NOT NULL"
        " AND ts >= ? AND date(ts,'unixepoch','localtime') >= date('now','localtime','-1 day')"
        " GROUP BY 1,2,3",
        (agora - 3 * 86400,),
    )
    apagados = con.execute("DELETE FROM eventos WHERE ts < ?", (agora - RETENCAO_DIAS * 86400,)).rowcount
    orfas = con.execute("DELETE FROM pendentes WHERE ts < ?", (agora - 86400,)).rowcount
    con.execute(
        "UPDATE sessoes SET estado='encerrada', fim=ultimo_ts WHERE estado!='encerrada' AND ultimo_ts < ?",
        (agora - 7 * 86400,),
    )
    con.execute("COMMIT")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    print(f"ok: resumo atualizado, {apagados} eventos antigos e {orfas} pendências órfãs apagados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
