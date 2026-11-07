#!/usr/bin/env python3
# Overlay d'entraînement Stockfish — lit une FEN ou un PGN copié (presse-papier)
# Lancer 1x = ouvre, relancer = cache/affiche (à lier à un raccourci KDE)
import sys, io, re, shutil
import chess, chess.pgn, chess.svg, chess.engine
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QByteArray
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

NAME = "chess-trainer"
THINK = 1.5          # secondes de calcul
LINES = 3            # nb de meilleurs coups
SIZE = 380           # taille échiquier px
COLORS = ["#00e676cc", "#ffab00aa", "#ff174488"]
BOARD_COLORS = {"square light": "#c9b6d9", "square dark": "#610089",
                "square light lastmove": "#e6d36a", "square dark lastmove": "#b39b2c"}


def parse(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return chess.Board(text)                      # FEN
    except ValueError:
        pass
    g = chess.pgn.read_game(io.StringIO(text))        # PGN
    if g is None or g.errors:
        return None
    if g.next() is None and "FEN" not in g.headers:
        return None
    return g.end().board()


def svg_alpha(svg):
    """QtSvg ne lit pas les couleurs #RRGGBBAA de python-chess (flèches noires) :
    on les sépare en couleur + opacité."""
    return re.sub(r'(stroke|fill)="#([0-9a-fA-F]{6})([0-9a-fA-F]{2})"',
                  lambda m: f'{m[1]}="#{m[2]}" {m[1]}-opacity="{int(m[3], 16) / 255:.2f}"', svg)


def fmt(score):
    s = score.white()
    return f"#{s.mate()}" if s.is_mate() else f"{s.score() / 100:+.2f}"


class Worker(QThread):
    done = pyqtSignal(object, object)

    def __init__(self, path):
        super().__init__()
        self.path, self.board, self.engine = path, None, None

    def run(self):
        if self.engine is None:
            self.engine = chess.engine.SimpleEngine.popen_uci(self.path)
        b = self.board
        infos = self.engine.analyse(b, chess.engine.Limit(time=THINK), multipv=LINES)
        self.done.emit(b, infos)


class Overlay(QWidget):
    def __init__(self, engine_path):
        super().__init__()
        self.setWindowTitle("Chess Trainer")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet("QWidget{background:#000;color:#eee;font:13px monospace}"
                           "#turn{font:bold 16px monospace;color:#d68cff}")
        self.board = self.infos = self.pending = None
        self.flip = False

        lay = QVBoxLayout(self)
        self.turn = QLabel("…"); self.turn.setObjectName("turn")
        self.svg = QSvgWidget(); self.svg.setFixedSize(SIZE, SIZE)
        self.info = QLabel(""); self.info.setWordWrap(True); self.info.setFixedWidth(SIZE)
        hint = QLabel("Esc cacher · R relire · F retourner · Q quitter · glisser = bouger")
        hint.setStyleSheet("color:#777;font-size:11px")
        for w in (self.turn, self.svg, self.info, hint):
            lay.addWidget(w)

        self.w = Worker(engine_path)
        self.w.done.connect(self.on_done)
        self.w.finished.connect(self.on_fin)

    # --- affichage ---
    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.show(); self.raise_(); self.activateWindow()
            QTimer.singleShot(150, self.reload)

    def reload(self):
        b = parse(QApplication.clipboard().text())
        if b is None:
            self.turn.setText("Presse-papier : pas de FEN/PGN")
            return
        self.board, self.infos = b, None
        self.draw()
        if b.is_game_over():
            self.info.setText(f"Partie finie : {b.result()}")
        else:
            self.pending = b.copy()
            if not self.w.isRunning():
                self.start()

    def start(self):
        self.w.board, self.pending = self.pending, None
        self.info.setText("Analyse…")
        self.w.start()

    def on_fin(self):
        if self.pending is not None:
            self.start()

    def on_done(self, b, infos):
        if self.board is not None and b.fen() == self.board.fen():
            self.infos = infos
            self.draw()

    def draw(self):
        b = self.board
        txt = "Trait : " + ("BLANCS" if b.turn else "NOIRS")
        if b.is_check():
            txt += "  — ÉCHEC"
        self.turn.setText(txt)

        arrows, lines = [], []
        for i, inf in enumerate(self.infos or []):
            pv = inf.get("pv")
            if not pv:
                continue
            m = pv[0]
            arrows.append(chess.svg.Arrow(m.from_square, m.to_square, color=COLORS[i % len(COLORS)]))
            lines.append(f"{i + 1}. {b.san(m):<7} {fmt(inf['score']):>7}   {b.variation_san(pv[:6])}")

        svg = chess.svg.board(b, orientation=b.turn ^ self.flip, arrows=arrows,
                              lastmove=b.peek() if b.move_stack else None,
                              size=SIZE, colors=BOARD_COLORS)
        self.svg.load(QByteArray(svg_alpha(svg).encode()))
        if self.infos is not None:
            self.info.setText("\n".join(lines) or "Aucun coup")

    # --- entrées ---
    def keyPressEvent(self, e):
        k, mod = e.key(), e.modifiers()
        if k == Qt.Key.Key_Escape:
            self.hide()
        elif k == Qt.Key.Key_R or (k == Qt.Key.Key_V and mod & Qt.KeyboardModifier.ControlModifier):
            self.reload()
        elif k == Qt.Key.Key_F and self.board:
            self.flip = not self.flip
            self.draw()
        elif k == Qt.Key.Key_Q:
            QApplication.quit()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.windowHandle():
            self.windowHandle().startSystemMove()   # marche sous Wayland


def main():
    app = QApplication(sys.argv)

    # déjà lancé ? -> toggle et sortir
    s = QLocalSocket()
    s.connectToServer(NAME)
    if s.waitForConnected(300):
        s.disconnectFromServer()
        return

    path = shutil.which("stockfish")
    if not path:
        sys.exit("stockfish introuvable : sudo pacman -S stockfish")

    QLocalServer.removeServer(NAME)
    srv = QLocalServer()
    srv.listen(NAME)
    ov = Overlay(path)

    def on_conn():
        c = srv.nextPendingConnection()
        if c:
            c.disconnectFromServer()
        ov.toggle()

    srv.newConnection.connect(on_conn)

    def cleanup():
        if ov.w.isRunning():
            ov.w.wait()
        if ov.w.engine:
            ov.w.engine.quit()

    app.aboutToQuit.connect(cleanup)
    ov.toggle()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
