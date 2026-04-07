#!/bin/bash

GEOM=1600x900

geom=$(grep -m1 '^\s*geometry' ~/.galapix/galapix.cfg | awk '{print $3}')

echo $geom | grep -q "[0-9]\+x[0-9]\+" || geom=$GEOM

IFS=""
for a in $*
do
  if ! echo $a | egrep -q '^\"?(\~|\/)'
	then
		args="$args $(echo file://\"$PWD/$a\")"
	elif echo $a | grep -q "\""
	then
		args="$args $(echo file://$a)"
	else
		args="$args $(echo file://\"$a\")"
	fi
done

rm -f ~/.galapix/other/cache3.sqlite3
ulimit -c 0
echo $args | xargs galapix.sdl --threads 6 view --background-color "#4b5262" --selection-border-color "#B02A37" --show-filenames -g $geom -d ~/.galapix/other/ --title "galapix: $args" &>/dev/null
