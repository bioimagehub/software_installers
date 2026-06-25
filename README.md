# software_installers
* This is a collection of software installers that will run/install python software with a single click
* Its using uv backend for making environments
* Default Python version is 3.10 unless a specific installer explicitly requires something else

# To install a program 
* and add it to windows start menu/desktop 
click on 'install_program_name.bat' (examle install_cellpose_2d.bat')

# For linux
I am sure you will figure it out. hint uv run ...

# Bugs
Please report bugs! This has only been tested on very few computers, and should run on most

# Debugging
You will need internet to use this, there is no way around it 

# Python version policy
* Keep `pyproject.toml` (`project.requires-python`) and `.python-version` aligned in each app folder
* Use Python 3.10 by default for new installers
* Only use another Python version when the target app requires it, and document that in the app README