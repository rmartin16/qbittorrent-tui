from setuptools import setup, find_packages

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name='qbittorrentui',
    version='0.1.3',
    packages=find_packages(exclude=["*.tests", "*.tests.*", "tests.*", "tests"]),
    package_data={'': ['default.ini']},
    include_package_data=True,
    install_requires=['urwid==2.1.2',
                      'panwid @ git+git://github.com/tonycpsu/panwid@v0.3.2.dev0',
                      'qbittorrent-api',
                      'blinker==1.4'],
    entry_points={
        'console_scripts': [
            'qbittorrentui = qbittorrentui.__main__:main'
        ]
    },
    url='https://github.com/rmartin16/qbittorrentui',
    author='Russell Martin',
    author_email='rmartin16@gmail.com',
    zip_safe=False,
    license='GPL-3',
    description='Console UI for qBittorrent v4.1+',
    long_description=long_description,
    long_description_content_type="text/markdown",
    keywords='qBittorrent console terminal TUI text',
    classifiers=["Programming Language :: Python :: 3.9",
                 "Programming Language :: Python :: 3.8",
                 "Programming Language :: Python :: 3.7",
                 "Programming Language :: Python :: 3.6",
                 "Environment :: Console",
                 'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
                 "Topic :: Communications :: File Sharing",
                 "Topic :: Utilities"]
)
