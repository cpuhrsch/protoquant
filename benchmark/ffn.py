import argparse
import csv
import itertools
import sys
import time
from functools import partial

import protoquant

import torch

import torch.utils.benchmark as benchmark


def benchmark_torch_function_in_microseconds(f, *args, **kwargs):
    t0 = benchmark.Timer(
        stmt="f(*args, **kwargs)", globals={"args": args, "kwargs": kwargs, "f": f}
    )
    return t0.blocked_autorange().mean * 1e6


class FFN(torch.nn.Module):
    def __init__(self, d_model, dim_feedforward, device, dtype):
        super(FFN, self).__init__()
        factory_kwargs = {"device": device, "dtype": dtype}
        self.linear1 = torch.nn.Linear(d_model, dim_feedforward, **factory_kwargs)
        self.activation = torch.nn.functional.relu
        self.linear2 = torch.nn.Linear(dim_feedforward, d_model, **factory_kwargs)

    def forward(self, x):
        # print("x.size(): ", x.size())
        # print("self.linear1.weight.size(): ", self.linear1.weight.size())
        x = self.linear1(x)
        x = self.activation(x)
        return self.linear2(x)


def run_benchmark(use_q, d_model, dim_feedforward, batch_size):
    seq_len = 256
    inp = torch.randn(batch_size, seq_len, d_model)
    inp = inp.half().cuda()
    ffn = FFN(
        d_model=d_model,
        dim_feedforward=dim_feedforward,
        device="cuda",
        dtype=torch.float16,
    )
    ffn = ffn.half().cuda().eval()
    fp16_ref = ffn(inp).detach().clone().float()
    if use_q:
        ffn.linear1.weight = torch.nn.Parameter(
            protoquant.QTensor(ffn.linear1.weight).force_quantize(is_a=False)
        )
        ffn.linear2.weight = torch.nn.Parameter(
            protoquant.QTensor(ffn.linear2.weight).force_quantize(is_a=False)
        )
        fp8_ref = ffn(inp).detach().clone().float()
        torch.testing.assert_close(fp16_ref, fp8_ref, atol=3e-2, rtol=3e-2)
    return benchmark_torch_function_in_microseconds(ffn, inp)


def get_default_shapes():
    for i, (d_model, dim_feedforward) in enumerate(
        itertools.product([1024, 2048, 4096, 8192], [1024, 2048, 4096, 8192])
    ):
        yield (d_model, dim_feedforward, f"default{i}")


def get_opt_shapes():
    d_model = [
        1536,
        2048,
        2560,
        4096,
        5120,
        7168,
        9216,
        12288,
    ]

    dim_feedforward = [
        6144,
        8192,
        10240,
        16384,
        20480,
        28672,
        36864,
        49152,
    ]

    annotation = [
        "760M",
        "1.3B",
        "2.7B",
        "6.7B",
        "13B",
        "30B",
        "66B",
        "175B",
    ]

    for d, f, a in zip(d_model, dim_feedforward, annotation):
        yield (d, f, a)


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("batchsize")
    parser.add_argument("--opt-shapes", action="store_true")
    args = parser.parse_args()

    headers = [
        "bs",
        "kind",
        "d_model",
        "dim_feedforward",
        "with_q(μs)",
        "without_q(μs)",
        "speedup",
    ]
    shape_gen = get_default_shapes
    if args.opt_shapes:
        shape_gen = get_opt_shapes
    print(",".join(headers))
    bs = int(args.batchsize)
    for d_model, dim_feedforward, annotation in shape_gen():
        with_q = run_benchmark(True, d_model, dim_feedforward, bs)
        without_q = run_benchmark(False, d_model, dim_feedforward, bs)
        print(
            ",".join(
                map(
                    str,
                    [
                        bs,
                        annotation,
                        d_model,
                        dim_feedforward,
                        f"{with_q:.0f}",
                        f"{without_q:.0f}",
                        f"{without_q / with_q:.2f}",
                    ],
                )
            )
        )
