cmake_minimum_required(VERSION 3.20.6...3.22.6 FATAL_ERROR)

project(MucusPlugNavigator)

#-----------------------------------------------------------------------------
# Extension meta-information
set(EXTENSION_HOMEPAGE "https://github.com/YOUR_GITHUB_NAME/MucusPlugNavigator")
set(EXTENSION_CONTRIBUTORS "Bohua Liu")
set(EXTENSION_DESCRIPTION "A 3D Slicer extension for lung CT mucus plug navigation, editing, counting, and scoring.")
set(EXTENSION_ICONURL "")
set(EXTENSION_SCREENSHOTURLS "")
set(EXTENSION_DEPENDS "NA") # Specified as a list or "NA" if no dependencies

#-----------------------------------------------------------------------------
# Extension dependencies
find_package(Slicer REQUIRED)
include(${Slicer_USE_FILE})

#-----------------------------------------------------------------------------
# Extension modules
add_subdirectory(MucusPlugNavigator)
## NEXT_MODULE

#-----------------------------------------------------------------------------
include(${Slicer_EXTENSION_GENERATE_CONFIG})
include(${Slicer_EXTENSION_CPACK})