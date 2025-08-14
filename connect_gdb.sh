#!/bin/bash

function die()
{
    echo $1
    exit 1
}

# echos either the second argument or the first argument if the second argument is empty
function default()
{
    if [ -z "$2" ]; then
        echo $1
    else
        echo $2
    fi
}

# Gets the value of an environment variable
function get_env_var()
{
    echo $(eval echo \$$1)
}

function do_cmd()
{
    local verbosity=$(get_env_var VERBOSE)
    local cmd="$@"

    if [ "$verbosity" == "1" ]; then
        echo $cmd
    fi

    eval $cmd

    return $?
}

# Does the main logic of the script
function launch_gdb()
{
    local script_dir=$(dirname $0)
    local gdb_script_default="${script_dir}/../zerOS/.gdbinit"
    local gdb_script=$(default $gdb_script_default $1)
    gdb_script=$(readlink -f $gdb_script)

    if [ ! -f $gdb_script ]; then
        die "Error: gdb script not found at $gdb_script"
    fi

    local arch_env=$(get_env_var ARCH)
    local arch=$(default "x86_64" $arch_env)

    local file_format_env=$(get_env_var FILE_FORMAT)
    local file_format=$(default "elf" $file_format_env)

    local gdb_env=$(get_env_var GDB)
    local gdb_default="${script_dir}/../toolchain/install/bin/${arch}-${file_format}-gdb"
    local gdb=$(default $gdb_default $gdb_env)
    gdb=$(readlink -f $gdb)

    local gdb_exec_dir=$(dirname $gdb_script)
    gdb_exec_dir=$(readlink -f $gdb_exec_dir)

    if [ ! -f $gdb ]; then
        die "Error: gdb not found at $gdb"
    fi

    do_cmd $gdb --cd="${gdb_exec_dir}" -x $gdb_script

    if [ $? -ne 0 ]; then
        die "Error: gdb failed"
    fi
}

# Main function
function main()
{
    launch_gdb "$@"
    exit 0
}

main "$@"
