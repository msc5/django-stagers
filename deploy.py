import os


if __name__ == "__main__":
    os.system('rm -rf dist/')
    os.system('python -m build')
    os.system('python -m twine upload --repository pypi dist/*')
