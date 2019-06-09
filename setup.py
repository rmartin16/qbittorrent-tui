from setuptools import setup, find_packages

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name='qbittorrentui',
    version='0.1',
    packages=find_packages(exclude=["*.tests", "*.tests.*", "tests.*", "tests"]),
    package_data={'': ['default.ini']},
    include_package_data=True,
    install_requires=['urwid==2.0.1',
                      'attrdict<=2.0.1,>=2.0.0',
                      'panwid==0.2.5',
                      'qbittorrent-api',
                      'blinker==1.4'
                      ],
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
    keywords='qbittorrent console terminal TUI text',
    classifiers=["Programming Language :: Python :: 3.7",
                 "Programming Language :: Python :: 3.6",
                 "Environment :: Console",
                 'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
                 "Topic :: Communications :: File Sharing",
                 "Topic :: Utilities",
                 ]
)
