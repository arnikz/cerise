#!/usr/bin/env cwl-runner

cwlVersion: v1.0

class: CommandLineTool
baseCommand: cat
arguments: [$CERISE_API_FILES/test/test_install_script.txt]

inputs: []

stdout: output.txt
outputs:
  output:
    type: File
    outputBinding: { glob: output.txt }
