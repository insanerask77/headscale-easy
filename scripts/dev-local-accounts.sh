#!/usr/bin/env bash
# Development helper for testing local accounts
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info() {
    echo -e "${BLUE}ℹ${NC} $*"
}

success() {
    echo -e "${GREEN}✓${NC} $*"
}

warn() {
    echo -e "${YELLOW}⚠${NC} $*"
}

error() {
    echo -e "${RED}✗${NC} $*" >&2
}

header() {
    echo ""
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE} $*${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# Check if container is running
check_container() {
    if ! docker compose ps | grep -q "headscale-easy.*Up"; then
        error "Web container is not running"
        info "Start it with: docker compose up -d"
        exit 1
    fi
}

# List all local accounts
list_accounts() {
    header "Local Accounts"
    check_container

    docker exec headscale-easy python3 -c "
import sys
sys.path.insert(0, '/app')
import local_accounts as la

la.configure('/data/console/accounts.db')
accounts = la.list_accounts()

if not accounts:
    print('No accounts found.')
else:
    print(f'Found {len(accounts)} account(s):\n')
    for acc in accounts:
        disabled = '(DISABLED)' if acc.get('disabled') else ''
        totp = '🔒 TOTP' if acc.get('totp_confirmed') else '  '
        print(f'{totp}  {acc[\"username\"]:<15} {acc[\"email\"]:<30} {acc[\"role\"]:<15} {disabled}')
"
}

# Create a new account
create_account() {
    header "Create New Account"
    check_container

    read -p "Username: " username
    read -p "Email: " email
    read -sp "Password (min 8 chars): " password
    echo ""

    echo "Role:"
    echo "  1) member (default)"
    echo "  2) admin"
    echo "  3) network_admin"
    echo "  4) auditor"
    read -p "Choose (1-4): " role_choice

    case $role_choice in
        2) role="admin" ;;
        3) role="network_admin" ;;
        4) role="auditor" ;;
        *) role="member" ;;
    esac

    docker exec headscale-easy python3 -c "
import sys
sys.path.insert(0, '/app')
import local_accounts as la

la.configure('/data/console/accounts.db')
try:
    account_id = la.create_account('$username', '$email', '$password', role='$role')
    print(f'\n✓ Created account ID: {account_id}')
    print(f'  Username: $username')
    print(f'  Email: $email')
    print(f'  Role: $role')
except ValueError as e:
    print(f'\n✗ Error: {e}')
    sys.exit(1)
"

    success "Account created successfully"
    info "Sign in at: http://vpn.127.0.0.1.nip.io/admin/login"
}

# Enable/disable account
toggle_account() {
    header "Enable/Disable Account"
    check_container

    docker exec headscale-easy python3 -c "
import sys
sys.path.insert(0, '/app')
import local_accounts as la

la.configure('/data/console/accounts.db')
accounts = la.list_accounts()

if not accounts:
    print('No accounts found.')
    sys.exit(0)

print('Accounts:')
for i, acc in enumerate(accounts, 1):
    status = 'DISABLED' if acc.get('disabled') else 'ENABLED'
    print(f'{i}) {acc[\"username\"]:<15} ({status})')

print()
"

    read -p "Account number: " acc_num
    read -p "Action (enable/disable): " action

    docker exec headscale-easy python3 -c "
import sys
sys.path.insert(0, '/app')
import local_accounts as la

la.configure('/data/console/accounts.db')
accounts = la.list_accounts()

try:
    account = accounts[int('$acc_num') - 1]
    if '$action' == 'disable':
        la.disable_account(account['id'])
        print(f'\n✓ Disabled account: {account[\"username\"]}')
    else:
        la.enable_account(account['id'])
        print(f'\n✓ Enabled account: {account[\"username\"]}')
except (IndexError, ValueError) as e:
    print(f'\n✗ Invalid selection')
    sys.exit(1)
"
}

# Change account role
change_role() {
    header "Change Account Role"
    check_container

    docker exec headscale-easy python3 -c "
import sys
sys.path.insert(0, '/app')
import local_accounts as la

la.configure('/data/console/accounts.db')
accounts = la.list_accounts()

if not accounts:
    print('No accounts found.')
    sys.exit(0)

print('Accounts:')
for i, acc in enumerate(accounts, 1):
    print(f'{i}) {acc[\"username\"]:<15} (current role: {acc[\"role\"]})')

print()
"

    read -p "Account number: " acc_num

    echo "New role:"
    echo "  1) member"
    echo "  2) admin"
    echo "  3) network_admin"
    echo "  4) auditor"
    read -p "Choose (1-4): " role_choice

    case $role_choice in
        1) new_role="member" ;;
        2) new_role="admin" ;;
        3) new_role="network_admin" ;;
        4) new_role="auditor" ;;
        *) error "Invalid choice"; exit 1 ;;
    esac

    docker exec headscale-easy python3 -c "
import sys
sys.path.insert(0, '/app')
import local_accounts as la

la.configure('/data/console/accounts.db')
accounts = la.list_accounts()

try:
    account = accounts[int('$acc_num') - 1]
    la.set_account_role(account['id'], '$new_role')
    print(f'\n✓ Changed role for {account[\"username\"]}: {account[\"role\"]} → $new_role')
except (IndexError, ValueError) as e:
    print(f'\n✗ Error: {e}')
    sys.exit(1)
"
}

# Reset password
reset_password() {
    header "Reset Password"
    check_container

    read -p "Username: " username
    read -sp "New password (min 8 chars): " new_password
    echo ""

    docker exec headscale-easy python3 -c "
import sys
sys.path.insert(0, '/app')
import local_accounts as la

la.configure('/data/console/accounts.db')
account = la.get_account(username='$username')

if not account:
    print(f'\n✗ Account not found: $username')
    sys.exit(1)

try:
    la.update_password(account['id'], '$new_password')
    print(f'\n✓ Password updated for: $username')
except ValueError as e:
    print(f'\n✗ Error: {e}')
    sys.exit(1)
"
}

# Run tests
run_tests() {
    header "Running Tests"

    info "Running local accounts unit tests..."
    python3 -m unittest tests.test_local_accounts -v

    echo ""
    info "Running security tests..."
    python3 -m unittest tests.test_security.LocalAccountSignin \
                        tests.test_security.SelfServiceAccount \
                        tests.test_security.SignInModes -v
}

# Rebuild web container
rebuild() {
    header "Rebuilding Web Container"

    info "Building..."
    docker compose build web

    info "Restarting..."
    docker compose up -d web

    success "Done! Watching logs (Ctrl+C to exit)..."
    docker compose logs -f web
}

# Show logs
logs() {
    docker compose logs -f web
}

# Show menu
show_menu() {
    header "Local Accounts Development Helper"

    echo "1) List all accounts"
    echo "2) Create new account"
    echo "3) Enable/disable account"
    echo "4) Change account role"
    echo "5) Reset password"
    echo "6) Run tests"
    echo "7) Rebuild web container"
    echo "8) Show logs"
    echo "9) Exit"
    echo ""
    read -p "Choose an option: " choice

    case $choice in
        1) list_accounts ;;
        2) create_account ;;
        3) toggle_account ;;
        4) change_role ;;
        5) reset_password ;;
        6) run_tests ;;
        7) rebuild ;;
        8) logs ;;
        9) exit 0 ;;
        *) error "Invalid option" ;;
    esac

    echo ""
    read -p "Press Enter to continue..."
    show_menu
}

# Main
main() {
    if [[ $# -eq 0 ]]; then
        show_menu
    else
        case $1 in
            list) list_accounts ;;
            create) create_account ;;
            toggle) toggle_account ;;
            role) change_role ;;
            reset) reset_password ;;
            test) run_tests ;;
            rebuild) rebuild ;;
            logs) logs ;;
            *)
                error "Unknown command: $1"
                echo ""
                echo "Usage: $0 [command]"
                echo ""
                echo "Commands:"
                echo "  list     - List all accounts"
                echo "  create   - Create new account"
                echo "  toggle   - Enable/disable account"
                echo "  role     - Change account role"
                echo "  reset    - Reset password"
                echo "  test     - Run tests"
                echo "  rebuild  - Rebuild web container"
                echo "  logs     - Show logs"
                echo ""
                echo "Run without arguments for interactive menu"
                exit 1
                ;;
        esac
    fi
}

main "$@"
