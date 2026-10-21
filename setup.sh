#!/usr/bin/env bash
#
# Orbital Intelligence System (OIS) - Linux Installation & Setup Script
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
#

set -Eeuo pipefail

# Colors
CLR_RESET="\033[0m"
CLR_BOLD="\033[1m"
CLR_DIM="\033[2m\033[38;5;244m"
CLR_CYAN="\033[38;5;51m"
CLR_CYAN_BOLD="\033[1;38;5;51m"
CLR_BLUE="\033[38;5;39m"
CLR_BLUE_BOLD="\033[1;38;5;39m"
CLR_GREEN="\033[38;5;48m"
CLR_GREEN_BOLD="\033[1;38;5;48m"
CLR_YELLOW="\033[38;5;220m"
CLR_YELLOW_BOLD="\033[1;38;5;220m"
CLR_RED="\033[38;5;203m"
CLR_RED_BOLD="\033[1;38;5;203m"
CLR_BORDER="\033[38;5;240m"

info()    { echo -e "  ${CLR_BLUE}[i]${CLR_RESET} $1"; }
success() { echo -e "  ${CLR_GREEN}[✔]${CLR_RESET} $1"; }
warn()    { echo -e "  ${CLR_YELLOW}[!]${CLR_RESET} $1"; }
error()   { echo -e "  ${CLR_RED}[x]${CLR_RESET} $1"; }
step()    { echo -e "\n${CLR_CYAN_BOLD}==>${CLR_RESET} ${CLR_BOLD}$1${CLR_RESET}"; }

on_error() {
    local exit_code=$?
    error "Setup failed at line ${BASH_LINENO[0]} (exit code ${exit_code})."
    exit "$exit_code"
}
trap on_error ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${CLR_CYAN}"
cat << 'ART'
                          :#%%                                           
                         **   **                                         
                       +%      :%+                                       
                     =%*        -%@-                                     
                   -%- :%=    :%+  :@:                                   
                  %=     :%-:%*      -%:                                 
                *#         *@:        +#:  -%+%-                         
                 :@:     +%  =%     :@:   %=   =@:                       
                   =%: =%-     ** :@%+  **       +#:                     
                     *@=        :@=   @%           ##                    
                      :#*      #*    @:             :@:                  
                        :%+  +# =%-@@@%=           +#                    
                          :@%:  :%-  -%:%-       =%:                     
                              :#+      *#-@-   -%-  :=                   
                             *#          #*=@:@%   %*:@=                 
                           +#              #+-  *%*    -%-               
                            %*           :#*   +%        =@:             
                 +*##**=   #*:%=        *#:%==%=%-     :%= +#:           
                  %=    :*%+@* -%-    -%:  :@-   =#: :**     #*          
                   :%-     :#*%+ #% :@:   %=       +%#:       :@:        
                     =%:     -@-#: *=    +#:      =%:#+      %=          
                     +*+%     :@           #*   -@-   :@=  %*            
                    +*=%-##    -#           :#+%=       -@#              
                   #+@=%*  #+   @             :%=      -%                
          :#      #@@#:     :%= %               -%-  -%-                 
           *=++%-%%:          -%@                 =%%=                   
            #++#+%+             :                                        
             -@-+%%:                                                     
               :#%*-                                                     
ART
echo -e "${CLR_RESET}"

echo -e "  ${CLR_BORDER}╭────────────────────────────────────────────────────────────────────────╮${CLR_RESET}"
echo -e "  ${CLR_BORDER}│${CLR_RESET}          ${CLR_CYAN_BOLD}🛰️   ORBITAL INTELLIGENCE SYSTEM - SETUP & INSTALL${CLR_RESET}          ${CLR_BORDER}│${CLR_RESET}"
echo -e "  ${CLR_BORDER}│${CLR_RESET}        ${CLR_DIM}Automated environment configuration & database bootstrap${CLR_RESET}        ${CLR_BORDER}│${CLR_RESET}"
echo -e "  ${CLR_BORDER}╰────────────────────────────────────────────────────────────────────────╯${CLR_RESET}"

# 1. System Requirements & Dependencies
step "[1/5] Checking system prerequisites..."

PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY_MAJOR=$("$candidate" -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo "0")
        PY_MINOR=$("$candidate" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo "0")
        if [ "$PY_MAJOR" -gt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -ge 14 ]; }; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    error "Python 3.14 or higher is required by pyproject.toml, but was not found."
    echo -e "      Install it via your distribution package manager:"
    echo -e "        - Ubuntu/Debian: sudo apt update && sudo apt install python3 python3-venv"
    echo -e "        - Arch/CachyOS:  sudo pacman -S python"
    echo -e "        - Fedora/RHEL:   sudo dnf install python3"
    exit 1
fi
success "Found Python $($PYTHON_BIN --version 2>&1) at $(command -v "$PYTHON_BIN")"

# 2. Environment Configuration (.env)
step "[2/5] Configuring environment settings..."

DB_PORT="3307"
if [ -f "docker-compose.yml" ]; then
    DETECTED_PORT=$(grep -oE '[0-9]+:3306' docker-compose.yml | head -n 1 | cut -d: -f1 || echo "")
    if [ -n "$DETECTED_PORT" ]; then
        DB_PORT="$DETECTED_PORT"
    fi
fi

# An existing .env is authoritative; otherwise use the Compose host port.
if [ -f ".env" ]; then
    ENV_PORT=$(sed -n 's/^OIS_DB_PORT=[[:space:]]*//p' .env | tail -n 1)
    if [[ "$ENV_PORT" =~ ^[0-9]+$ ]]; then
        DB_PORT="$ENV_PORT"
    fi
fi

if [ ! -f ".env" ]; then
    info "Creating .env configuration file (DB Port: ${DB_PORT})..."
    cat << ENV_EOF > .env
OIS_DB_HOST=127.0.0.1
OIS_DB_PORT=${DB_PORT}
OIS_DB_USER=ois
OIS_DB_PASSWORD=ois
OIS_DB_NAME=ois_db
ENV_EOF
    success "Created .env"
else
    success "Found existing .env"
fi

# 3. Database & Container Verification
step "[3/5] Verifying MariaDB database service..."

check_db_port() {
    "$PYTHON_BIN" -c "
import socket, sys
try:
    s = socket.create_connection(('127.0.0.1', ${DB_PORT}), timeout=2)
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

if check_db_port; then
    success "MariaDB is already listening on port ${DB_PORT}"
else
    info "MariaDB is not reachable on port ${DB_PORT}. Starting Docker container..."
    
    DOCKER_CMD=""
    if docker compose version >/dev/null 2>&1; then
        DOCKER_CMD="docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        DOCKER_CMD="docker-compose"
    elif command -v podman-compose >/dev/null 2>&1; then
        DOCKER_CMD="podman-compose"
    elif command -v podman >/dev/null 2>&1; then
        DOCKER_CMD="podman compose"
    fi

    if [ -z "$DOCKER_CMD" ]; then
        warn "No Docker or Podman command found."
        echo -e "      Please ensure MariaDB is running on 127.0.0.1:${DB_PORT}."
    else
        info "Running: $DOCKER_CMD up -d"
        if ! $DOCKER_CMD up -d 2>/dev/null; then
            warn "$DOCKER_CMD failed. Trying with sudo or checking permissions..."
            if command -v sudo >/dev/null 2>&1; then
                sudo $DOCKER_CMD up -d || true
            fi
        fi
    fi

    info "Waiting for MariaDB port ${DB_PORT} to accept connections..."
    TRIES=0
    MAX_TRIES=25
    until check_db_port || [ $TRIES -ge $MAX_TRIES ]; do
        sleep 1
        TRIES=$((TRIES + 1))
        echo -n "."
    done
    echo ""

    if check_db_port; then
        success "MariaDB container is online and accepting connections."
    else
        warn "MariaDB is not answering yet on port ${DB_PORT}."
        echo -e "      If starting Docker container for the first time, it may take another moment."
        echo -e "      You can start it manually with: docker compose up -d"
    fi
fi

# 4. Python Virtual Environment & Packages
step "[4/5] Setting up Python virtual environment..."

if [ ! -d ".venv" ]; then
    if command -v uv >/dev/null 2>&1; then
        info "Creating virtual environment using uv..."
        uv venv .venv
        info "Installing dependencies via uv..."
        uv pip install --python .venv/bin/python -r requirements.txt
    else
        info "Creating virtual environment using python3 -m venv..."
        if ! "$PYTHON_BIN" -m venv .venv 2>/dev/null; then
            error "Failed to create virtual environment."
            echo -e "      On Debian/Ubuntu, run: sudo apt install python3-venv"
            exit 1
        fi
        info "Installing dependencies via pip..."
        .venv/bin/python -m pip install --upgrade pip
        .venv/bin/python -m pip install -r requirements.txt
    fi
    success "Virtual environment ready at .venv/"
else
    success "Existing virtual environment found at .venv/"
    if command -v uv >/dev/null 2>&1; then
        uv pip install --python .venv/bin/python -r requirements.txt
    else
        .venv/bin/python -m pip install -r requirements.txt
    fi
fi

# 5. Database Schema & Data Ingestion
step "[5/5] Initializing database schema & syncing telemetry..."

if check_db_port; then
    info "Applying schema.sql and seed.sql..."
    .venv/bin/python -m app --init-db
    success "Database schema and seed applied."

    info "Ingesting initial satellite catalog and debris telemetry..."
    if .venv/bin/python -m app --sync; then
        success "Satellite data ingestion complete."
    else
        warn "Initial data sync failed; you can retry later with ./ois --sync."
    fi

    info "Calculating initial proximity and collision alerts..."
    if .venv/bin/python -m app --alerts >/dev/null 2>&1; then
        success "Collision alerts precalculated."
    else
        warn "Could not precalculate alerts; run ./ois --alerts after syncing data."
    fi
else
    warn "Skipping database population since MariaDB is not yet connected."
    echo -e "      Run later: ./ois --init-db && ./ois --sync"
fi

# Create local ./ois launcher
cat << 'RUNNER_EOF' > "$SCRIPT_DIR/ois"
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"
exec "$SCRIPT_DIR/.venv/bin/python" -m app "$@"
RUNNER_EOF
chmod +x "$SCRIPT_DIR/ois"
success "Created local runner: ./ois"

# Install global launcher in ~/.local/bin if possible
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"
cat << GLOBAL_EOF > "$LOCAL_BIN/ois"
#!/usr/bin/env bash
PROJECT_DIR="$SCRIPT_DIR"
export PYTHONPATH="\$PROJECT_DIR:\$PYTHONPATH"
exec "\$PROJECT_DIR/.venv/bin/python" -m app "\$@"
GLOBAL_EOF
chmod +x "$LOCAL_BIN/ois"
ln -sf "$LOCAL_BIN/ois" "$LOCAL_BIN/satellites"
success "Installed launcher: $LOCAL_BIN/ois (and symlink 'satellites')"

# PATH check
PATH_HINT=""
if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
    PATH_HINT="\n  ${CLR_YELLOW}[!] Note: $LOCAL_BIN is not currently in your \$PATH.${CLR_RESET}\n      Add this line to your ~/.bashrc or ~/.zshrc:\n      export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo -e "\n  ${CLR_GREEN_BOLD}════════════════════════════════════════════════════════════════════════${CLR_RESET}"
echo -e "  ${CLR_GREEN_BOLD}✔  SETUP AND INSTALLATION COMPLETE!${CLR_RESET}"
echo -e "  ${CLR_GREEN_BOLD}════════════════════════════════════════════════════════════════════════${CLR_RESET}"
echo -e "  You can now launch the app from anywhere:"
echo -e "    ${CLR_CYAN_BOLD}ois${CLR_RESET}                  Launch Mission Control interactive console"
echo -e "    ${CLR_CYAN_BOLD}satellites${CLR_RESET}           Alias for ois"
echo -e "    ${CLR_CYAN_BOLD}./ois${CLR_RESET}                Direct local launcher from this repository"
echo -e ""
echo -e "  Common command examples:"
echo -e "    ${CLR_BOLD}ois --alerts${CLR_RESET}         View active collision & proximity alerts"
echo -e "    ${CLR_BOLD}ois --view 25544${CLR_RESET}     Inspect the International Space Station"
echo -e "    ${CLR_BOLD}ois --report operator${CLR_RESET}   Fleet distribution breakdown"
echo -e "    ${CLR_BOLD}ois --art${CLR_RESET}            Showcase the satellite ASCII art"
echo -e "$PATH_HINT"
echo ""

# Interactive prompt to launch
if [ -t 0 ] && [ -t 1 ]; then
    read -r -p "  Launch Orbital Intelligence System now? [Y/n]: " LAUNCH_CHOICE
    LAUNCH_CHOICE=${LAUNCH_CHOICE:-Y}
    if [[ "$LAUNCH_CHOICE" =~ ^[Yy]$ ]]; then
        exec "$SCRIPT_DIR/ois"
    fi
fi
