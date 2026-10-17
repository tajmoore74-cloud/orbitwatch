# Optional: a single-node k3s cluster on one small EC2 instance.
#
# COST WARNING: a t3.small is NOT free-tier and costs roughly $15/month if
# left running. Run `terraform destroy` when you're done demoing.
# (A t3.micro is free-tier eligible but has only 1 GB RAM, which is too
# tight for k3s plus Prometheus.)

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "orbitwatch"
      ManagedBy = "terraform"
    }
  }
}

variable "region" {
  type    = string
  default = "us-west-2"
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "my_ip_cidr" {
  description = "Your public IP in CIDR form, e.g. 203.0.113.10/32. Only this IP can reach SSH and the Kubernetes API."
  type        = string
}

variable "ssh_public_key" {
  description = "Contents of your SSH public key (~/.ssh/id_ed25519.pub)"
  type        = string
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

data "aws_vpc" "default" {
  default = true
}

resource "aws_key_pair" "k3s" {
  key_name   = "orbitwatch-k3s"
  public_key = var.ssh_public_key
}

resource "aws_security_group" "k3s" {
  name        = "orbitwatch-k3s"
  description = "SSH and Kubernetes API from my IP only"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.my_ip_cidr]
  }

  ingress {
    description = "Kubernetes API"
    from_port   = 6443
    to_port     = 6443
    protocol    = "tcp"
    cidr_blocks = [var.my_ip_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "k3s" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.k3s.key_name
  vpc_security_group_ids = [aws_security_group.k3s.id]

  metadata_options {
    http_tokens = "required" # IMDSv2 only
  }

  root_block_device {
    volume_size = 20
    encrypted   = true
  }

  # Installs k3s on first boot. --tls-san lets you reach the API by public IP.
  user_data = <<-EOF
    #!/bin/bash
    set -euo pipefail
    PUBLIC_IP=$(curl -s -H "X-aws-ec2-metadata-token: $(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')" http://169.254.169.254/latest/meta-data/public-ipv4)
    curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--tls-san $PUBLIC_IP --write-kubeconfig-mode 644" sh -
  EOF

  tags = {
    Name = "orbitwatch-k3s"
  }
}

output "public_ip" {
  value = aws_instance.k3s.public_ip
}

output "get_kubeconfig" {
  value = "ssh ubuntu@${aws_instance.k3s.public_ip} 'cat /etc/rancher/k3s/k3s.yaml' | sed 's/127.0.0.1/${aws_instance.k3s.public_ip}/' > ~/.kube/orbitwatch.yaml"
}
