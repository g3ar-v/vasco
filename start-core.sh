#!/usr/bin/env bash
SOURCE="$0"

script=${0}
# echo  "script: $script"
script=${script##*/}
# echo "$script"
# NOTE: for macOS, it seems I have to leave script folder to run the other scripts
if [[ "$script" != "start-core.sh" ]]; then
	# echo "going back .."
	cd -P "$(dirname "$SOURCE")"/.. || exit 1 # Enter scripts folder or fail!
else
	cd -P "$(dirname "$SOURCE")" || exit 1 # Enter scripts folder or fail!
fi

DIR="$(pwd)"
UI_DIR="$(pwd)/core/ui"
export CONDA_ENV_NAME="core"

function found_exe() {
	hash "$1" 2>/dev/null
}

if found_exe tput; then
	if [[ $(tput colors) != "-1" && -z $CI ]]; then
		GREEN=$(tput setaf 2)
		BLUE=$(tput setaf 4)
		CYAN=$(tput setaf 6)
		YELLOW=$(tput setaf 3)
		RED=$(tput setaf 1)
		RESET=$(tput sgr0)
		HIGHLIGHT=$YELLOW
	fi
fi

help() {
	echo "${script}:  core command/service launcher"
	echo "usage: ${script} [COMMAND] [restart] [params]"
	echo
	echo "Services COMMANDs:"
	echo "  all                      runs core services: bus, audio, core, listener"
	echo "  debug                    runs core services, then starts the CLI"
	echo "  audio                    the audio playback service"
	echo "  bus                      the messagebus service"
	echo "  core                     the core service"
	echo "  listener                    listener capture service"
	# echo "  enclosure                enclosure service"
	echo
	echo "Tool COMMANDs:"
	echo "  cli                      the Command Line Interface"
	echo " 	web                		 the web UI service"
	# echo "  unittest                 run core unit tests (requires pytest)"
	# echo "  skillstest               run the skill autotests for all skills (requires pytest)"
	# echo "  vktest                   run the Voight Kampff integration test suite"
	echo
	# echo "Util COMMANDs:"
	# echo "  audiotest                attempt simple audio validation"
	# echo "  wakewordtest             test selected wakeword engine"
	# echo "  sdkdoc                   generate sdk documentation"
	echo
	echo "Options:"
	echo "  restart                  (optional) Force the service to restart if running"
	echo
	echo "Examples:"
	echo "  ${script} all"
	echo "  ${script} all restart"
	echo "  ${script} cli"
	# echo "  ${script} unittest"

	exit 1
}

_module=""
name_to_script_path() {
	case ${1} in
	"bus") _module="source.messagebus.service" ;;
	"core") _module="source.core" ;;
	"audio") _module="source.audio" ;;
	"listener") _module="source.client.listener" ;;
	"cli") _module="source.client.text" ;;
	"web") _module="source.client.web" ;;
	# "audiotest")         _module="core.util.audio_test" ;;
	# "wakewordtest")      _module="test.wake_word" ;;
	# "enclosure")         _module="core.client.enclosure" ;;

	*)
		echo "Error: Unknown name '${1}'"
		exit 1
		;;
	esac

}

source_venv() {
	# Enter CONDA virtual environment
	# TODO: dynmaically get miniconda path
	CONDA_BIN=$(dirname "$CONDA_EXE")
	# $CONDA_EXE activate $CONDA_ENV_NAME
	source "$CONDA_BIN/activate" $CONDA_ENV_NAME
	exit_status=$?
	if [[ $exit_status -eq "0" ]]; then
		echo $BLUE "Entering virtual environment ${CONDA_DEFAULT_ENV} $RESET"
	else
		echo $RED "Could not enter virtual environment $RESET"
	fi
}

first_time=true
init_once() {
	if ($first_time); then
		echo "Initializing..."
		# "${DIR}/scripts/prepare-msm.sh"
		source_venv
		first_time=false
	fi
}

launch_process() {
	init_once

	name_to_script_path "${1}"

	# Launch process in foreground
	echo "Starting $1"
	python -m ${_module} "$@"
}

require_process() {
	# Launch process if not found
	name_to_script_path "${1}"
	if ! pgrep -f "(python3|python|Python) (.*)-m ${_module}" >/dev/null; then
		# Start required process
		launch_background "${1}"
	fi
}

launch_background() {
	init_once

	# Check if given module is running and start (or restart if running)
	name_to_script_path "${1}"
	if pgrep -f "(python3|python|Python) (.*)-m ${_module}" >/dev/null; then
		if ($_force_restart); then
			echo "Restarting: ${1}"
			"${DIR}/stop-core.sh" "${1}"
		else
			# Already running, no need to restart
			return
		fi
	else
		echo "Starting background service $1"
	fi

	# Security warning/reminder for the user
	if [ "${1}" = "bus" ]; then
		echo "$HIGHLIGHT CAUTION: The core bus is an open websocket with no built-in security"
		echo "         measures.  You are responsible for protecting the local port"
		echo "         8181 with a firewall as appropriate. $RESET "
	fi

	# Launch process in background, sending logs to standard location
	python -m ${_module} "$@" >>"/var/log/core/${1}.log" 2>&1 &
}

launch_all() {
	echo "Starting all core services"
	launch_background bus
	launch_background audio
	# NOTE: provide the opportunity for online listener component to load
	sleep 2 # Add a delay of 5 seconds
	launch_background listener
	launch_background core
	# FIX: issue with websocket being none-type at startup
	launch_background web
	# launch_background enclosure
	# cd "${UI_DIR}" || exit
	# nohup npm run dev &
	# cd ..

	# nohup uvicorn core.ui.backend.__main__:app &

}

check_dependencies() {
	if [ -f .dev_opts.json ]; then
		auto_update=$(jq -r ".auto_update" <.dev_opts.json 2>/dev/null)
	else
		auto_update="false"
	fi
	if [ "$auto_update" = "true" ]; then
		# Check github repo for updates (e.g. a new release)
		git pull
	fi

	if [ ! -f .installed ] || ! md5sum -c >/dev/null 2>&1 <.installed; then
		# Critical files have changed, dev_setup.sh should be run again
		if [ "$auto_update" = "true" ]; then
			echo "Updating dependencies..."
			bash dev_setup.sh
		else
			echo "Please update dependencies by running ./dev_setup.sh again."
			if command -v notify-send >/dev/null; then
				# Generate a desktop notification (ArchLinux)
				notify-send "core Dependencies Outdated" "Run ./dev_setup.sh again"
			fi
			exit 1
		fi
	fi
}

_opt=$1
_force_restart=false

if [ $# -eq 0 ]; then
	help
	return
fi

shift
if [ "${1}" = "restart" ] || [ "${_opt}" = "restart" ]; then
	_force_restart=true
	if [ "${_opt}" = "restart" ]; then
		# Support "start-core.sh restart all" as well as "start-core.sh all restart"
		_opt=$1
	fi

	if [ $# -gt 0 ]; then
		shift
	fi
fi

# if [ ! "${_opt}" = "cli" ] ; then
#     # check_dependencies
# fi

case ${_opt} in
"all")
	launch_all
	;;

"bus")
	launch_background "${_opt}"
	;;
"audio")
	launch_background "${_opt}"
	;;
"core")
	launch_background "${_opt}"
	;;
"listener")
	launch_background "${_opt}"
	;;
"web")
	launch_background "${_opt}"
	;;
"debug")
	# launch_process cli
	launch_all
	# launch_process web
	launch_process cli
	;;

"cli")
	require_process bus
	# require_process web
	require_process core
	launch_process "${_opt}"
	;;

# TODO: Restore support for Wifi Setup on a Picroft, etc.
# "wifi")
#    launch_background ${_opt}
#    ;;
"unittest")
	source_venv
	pytest test/unittests/ --cov=core "$@"
	;;
"singleunittest")
	source_venv
	pytest "$@"
	;;
"skillstest")
	source_venv
	pytest test/integrationtests/skills/discover_tests.py "$@"
	;;
"vktest")
	"$DIR/bin/core-skill-testrunner" vktest "$@"
	;;
"audiotest")
	launch_process "${_opt}"
	;;
"wakewordtest")
	launch_process "${_opt}"
	;;
"enclosure")
	launch_background "${_opt}"
	;;

*)
	help
	;;
esac
