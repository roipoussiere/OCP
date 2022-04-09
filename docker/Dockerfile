# Build directory should include setup.py and Miniforge.sh.
FROM debian:sid

RUN set -eux; \
	apt-get update; \
	apt-get install -y --no-install-recommends \
		libgl1-mesa-glx \
		patchelf \
		wget \
		ca-certificates \
	; \
	rm -rf /var/lib/apt/lists/*

WORKDIR /workdir
RUN wget -O Miniforge3.sh "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-$(uname)-$(uname -m).sh"
RUN bash Miniforge3.sh -b -u

RUN . /root/miniforge3/bin/activate; \
    conda install -y ocp=7.5.3.0

# conda-forge packages of auditwheel and build are old
RUN . /root/miniforge3/bin/activate; \
    pip install --upgrade pip; \
    pip install auditwheel build

COPY setup.py .

RUN . /root/miniforge3/bin/activate; \
    python -m build --no-isolation

RUN ln -s dist/ocpvtk-*.whl /ocpvtk-linux.whl
