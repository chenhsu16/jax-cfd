"""Custom neural-net layers for physics simulations."""

import functools
from typing import (
    Any, Callable, Dict, Optional, Sequence, Tuple, TypeVar, Union,
)

import haiku as hk
import jax
from jax import lax
import jax.numpy as jnp
from jax_cfd.base import array_utils
from jax_cfd.base import boundaries
from jax_cfd.base import grids
from jax_cfd.ml import layers_util
from jax_cfd.ml import tiling
import numpy as np
import scipy.linalg

Array = Union[np.ndarray, jax.Array]
IntOrSequence = Union[int, Sequence[int]]


class PeriodicConvGeneral(hk.Module):
  """General periodic convolution module."""

  def __init__(
      self,
      base_convolution: Callable[..., Any],
      output_channels: int,
      kernel_shape: Tuple[int, ...],
      rate: int = 1,
      tile_layout: Optional[Tuple[int, ...]] = None,
      name: str = 'periodic_conv_general',
      **conv_kwargs: Any
  ):
    """Constructs PeriodicConvGeneral module.

    We use `VALID` padding on `base_convolution` and explicit padding to achieve
    the effect of periodic boundary conditions. This function computes
    `paddings` and combines `jnp.pad` function calls with `base_convolution`
    module to produce the dersired effect.

    Args:
      base_convolution: standard convolution module e.g. hk.Conv1D.
      output_channels: number of output channels.
      kernel_shape: shape of the kernel, compatible with `base_convolution`.
      rate: dilation rate of the convolution.
      tile_layout: optional layout for tiling spatial dimensions in a batch.
      name: name of the module.
      **conv_kwargs: additional arguments passed to `base_convolution`.
    """
    super().__init__(name=name)
    self._padding = []
    for kernel_size in kernel_shape:
      effective_kernel = kernel_size + (rate - 1) * (kernel_size - 1)
      pad_left = effective_kernel // 2
      self._padding.append((pad_left, effective_kernel - pad_left - 1))
    self._tile_layout = tile_layout
    self._conv_module = base_convolution(
        output_channels=output_channels, kernel_shape=kernel_shape,
        padding='VALID', rate=rate, **conv_kwargs)

  def __call__(self, inputs):
    return tiling.apply_convolution(
        self._conv_module, inputs, self._tile_layout, self._padding)


class PeriodicConv1D(PeriodicConvGeneral):
  """Periodic convolution module in 1D."""

  def __init__(
      self,
      output_channels: int,
      kernel_shape: Tuple[int],
      rate: int = 1,
      tile_layout: Optional[Tuple[int]] = None,
      name='periodic_conv_1d',
      **conv_kwargs
  ):
    """Constructs PeriodicConv1D module."""
    super().__init__(
        base_convolution=hk.Conv1D,
        output_channels=output_channels,
        kernel_shape=kernel_shape,
        rate=rate,
        tile_layout=tile_layout,
        name=name,
        **conv_kwargs
    )


class PeriodicConv2D(PeriodicConvGeneral):
  """Periodic convolution module in 2D."""

  def __init__(
      self,
      output_channels: int,
      kernel_shape: Tuple[int, int],
      rate: int = 1,
      tile_layout: Optional[Tuple[int, int]] = None,
      name='periodic_conv_2d',
      **conv_kwargs
  ):
    """Constructs PeriodicConv2D module."""
    super().__init__(
        base_convolution=hk.Conv2D,
        output_channels=output_channels,
        kernel_shape=kernel_shape,
        rate=rate,
        tile_layout=tile_layout,
        name=name,
        **conv_kwargs
    )


class PeriodicConv3D(PeriodicConvGeneral):
  """Periodic convolution module in 3D."""

  def __init__(
      self,
      output_channels: int,
      kernel_shape: Tuple[int, int, int],
      rate: int = 1,
      tile_layout: Optional[Tuple[int, int, int]] = None,
      name='periodic_conv_3d',
      **conv_kwargs
  ):
    """Constructs PeriodicConv3D module."""
    super().__init__(
        base_convolution=hk.Conv3D,
        output_channels=output_channels,
        kernel_shape=kernel_shape,
        rate=rate,
        tile_layout=tile_layout,
        name=name,
        **conv_kwargs
    )


class MirrorConvGeneral(hk.Module):
  """General periodic convolution module."""

  def __init__(self,
               base_convolution: Callable[..., Any],
               output_channels: int,
               kernel_shape: Tuple[int, ...],
               rate: int = 1,
               tile_layout: Optional[Tuple[int, ...]] = None,
               name: str = 'mirror_conv_general',
               **conv_kwargs: Any):
    """Constructs MirrorConvGeneral module.

    We use `VALID` padding on `base_convolution` and explicit padding beyond
    the boudaries. This function computes paddings` and combines `jnp.pad` 
    function calls with `base_convolution` module.

    Args:
      base_convolution: standard convolution module e.g. hk.Conv1D.
      output_channels: number of output channels.
      kernel_shape: shape of the kernel, compatible with `base_convolution`.
      rate: dilation rate of the convolution.
      tile_layout: optional layout for tiling spatial dimensions in a batch.
      name: name of the module.
      **conv_kwargs: additional arguments passed to `base_convolution`.
    """
    super().__init__(name=name)
    self._padding = np.zeros(2).astype('int')
    for kernel_size in kernel_shape:
      effective_kernel = kernel_size + (rate - 1) * (kernel_size - 1)
      pad_left = effective_kernel // 2
      self._padding += np.array([pad_left,
                                 effective_kernel - pad_left - 1]).astype('int')
    self._tile_layout = tile_layout
    self._conv_module = base_convolution(
        output_channels=output_channels, kernel_shape=kernel_shape,
        padding='VALID', rate=rate, **conv_kwargs)

  def _expand_var(self, var):
    var = var.bc.pad_all(
        var, (self._padding,) * var.grid.ndim, mode=boundaries.Padding.MIRROR)
    return jnp.expand_dims(var.data, axis=-1)

  def __call__(self, inputs):
    input_data = tuple(self._expand_var(var) for var in inputs)
    input_data = array_utils.concat_along_axis(
        jax.tree.leaves(input_data), axis=-1)
    outputs = self._conv_module(input_data)
    outputs = array_utils.split_axis(outputs, -1)
    outputs = tuple(
        var_input.bc.impose_bc(
            grids.GridArray(var, var_input.offset, var_input.grid))
        for var, var_input in zip(outputs, inputs))
    return outputs


class MirrorConv2D(MirrorConvGeneral):
  """Mirror convolution module in 2D."""

  def __init__(self,
               output_channels: int,
               kernel_shape: Tuple[int, int],
               rate: int = 1,
               tile_layout: Optional[Tuple[int, int]] = None,
               name='mirror_conv_2d',
               **conv_kwargs):
    """Constructs MirrorConv2D module."""
    super().__init__(
        base_convolution=hk.Conv2D,
        output_channels=output_channels,
        kernel_shape=kernel_shape,
        rate=rate,
        tile_layout=tile_layout,
        name=name,
        **conv_kwargs)


class PeriodicConvTransposeGeneral(hk.Module):
  """General periodic transpose convolution module."""

  def __init__(
      self,
      base_convolution: Callable[..., Any],
      output_channels: int,
      kernel_shape: Tuple[int, ...],
      stride: int = 1,
      tile_layout: Optional[Tuple[int, ...]] = None,
      name: str = 'periodic_conv_transpose_general',
      **conv_kwargs: Any
  ):
    """Constructs PeriodicConvTransposeGeneral module.

    To achieve the effect of periodic convolutions we first pad the inputs at
    the start of each spatial axis with wrap mode to ensure that the output
    generated by the original slice of the inputs receive contributions from
    periodic images when the `base_convolution` is applied. The
    `base_convolution` is applied with `VALID` padding followed by slicing
    to discard the boundary values. Additionally we perform a roll on the output
    to avoid the drift of spatial axes. (in standard implementation of the
    transposed convolutions the kernel applied to index [i] affects outputs at
    indicdes [i: i + kernel_size]. We perceive the center of the affected field
    as the spatial location of the output and hence shift it back by half of
    the kernel size.)

    Args:
      base_convolution: standard transpose convolution module.
      output_channels: number of output channels.
      kernel_shape: shape of the kernel, compatible with `base_convolution`.
      stride: stride to use in `base_convolution`.
      tile_layout: optional layout for tiling spatial dimensions in a batch.
      name: name of the module.
      **conv_kwargs: additional arguments passed to `base_convolution`.
    """
    if tile_layout is not None:
      raise NotImplementedError(
          "tile_layout doesn't work yet for transpose convolutions")
    super().__init__(name=name)
    self._stride = stride
    self._kernel_shape = kernel_shape
    self._padding = []
    self._roll_shifts = []
    for kernel_size in kernel_shape:
      # left pad should be large enough so that contribution from the leftmost
      # element just affect the output of the input's original first index: i.e.
      # stride * left_pad (output of the first index) should be less or equal to
      # kernel_size (last affected value of the input)
      pad_left = kernel_size // stride + 1
      self._padding.append((pad_left, 0))
      # we shift by half a kernel size at the end to recover spatial alignment.
      self._roll_shifts.append(-((kernel_size - 1) // 2))
    self._tile_layout = tile_layout
    self._conv_module = base_convolution(
        output_channels=output_channels, kernel_shape=kernel_shape,
        stride=stride, padding='VALID', **conv_kwargs)

  def __call__(self, inputs):
    """Applies PeriodicTransposeConvolution to `inputs`.

    Args:
      inputs: array with spatial and channel axes to which
        PeriodicTransposeConvolution is applied.

    Returns:
      `inputs` convolved with the kernel of the module with periodic padding.
    """
    ndim = len(self._kernel_shape)
    output_slice = []
    for axis, (left_pad, _) in enumerate(self._padding[:ndim]):
      axis_size = inputs.shape[axis]
      output_start = self._stride * left_pad
      output_end = self._stride * (axis_size + left_pad)
      output_slice.append(slice(output_start, output_end))
    output_slice.append(slice(None, None))
    output = tiling.apply_convolution(
        self._conv_module, inputs, self._tile_layout, self._padding)
    sliced_output = output[tuple(output_slice)]
    return jnp.roll(sliced_output, self._roll_shifts, list(range(ndim)))


class PeriodicConvTranspose1D(PeriodicConvTransposeGeneral):
  """Periodic transpose convolution module in 1D."""

  def __init__(
      self,
      output_channels: int,
      kernel_shape: Tuple[int],
      stride: int = 1,
      tile_layout: Optional[Tuple[int]] = None,
      name='periodic_conv_transpose_1d',
      **conv_kwargs
  ):
    """Constructs PeriodicConv1D module."""
    super().__init__(
        base_convolution=hk.Conv1DTranspose,
        output_channels=output_channels,
        kernel_shape=kernel_shape,
        stride=stride,
        tile_layout=tile_layout,
        name=name,
        **conv_kwargs
    )


class PeriodicConvTranspose2D(PeriodicConvTransposeGeneral):
  """Periodic transpose convolution module in 2D."""

  def __init__(
      self,
      output_channels: int,
      kernel_shape: Tuple[int, int],
      stride: int = 1,
      tile_layout: Optional[Tuple[int, int]] = None,
      name='periodic_conv_transpose_2d',
      **conv_kwargs
  ):
    """Constructs PeriodicConv2D module."""
    super().__init__(
        base_convolution=hk.Conv2DTranspose,
        output_channels=output_channels,
        kernel_shape=kernel_shape,
        stride=stride,
        tile_layout=tile_layout,
        name=name,
        **conv_kwargs
    )


class PeriodicConvTranspose3D(PeriodicConvTransposeGeneral):
  """Periodic transpose convolution module in 3D."""

  def __init__(
      self,
      output_channels: int,
      kernel_shape: Tuple[int, int, int],
      stride: int = 1,
      tile_layout: Optional[Tuple[int, int, int]] = None,
      name='periodic_conv_transpose_3d',
      **conv_kwargs
  ):
    """Constructs PeriodicConv3D module."""
    super().__init__(
        base_convolution=hk.Conv3DTranspose,
        output_channels=output_channels,
        kernel_shape=kernel_shape,
        stride=stride,
        tile_layout=tile_layout,
        name=name,
        **conv_kwargs
    )


def rescale_to_range(
    inputs: Array,
    min_value: float,
    max_value: float,
    axes: Tuple[int, ...]
) -> Array:
  """Rescales inputs to [min_value, max_value] range.

  Note that this function performs input dependent transformation, which might
  not be suitable for models that aim to learn different dynamics for different
  scales.

  Args:
    inputs: array to be rescaled to [min_value, max_value] range.
    min_value: value to which the smallest entry of `inputs` is mapped to.
    max_value: value to which the largest entry of `inputs` is mapped to.
    axes: `inputs` axes across which we search for smallest and largest values.

  Returns:
    `inputs` rescaled to [min_value, max_value] range.
  """
  inputs_max = jnp.max(inputs, axis=axes, keepdims=True)
  inputs_min = jnp.min(inputs, axis=axes, keepdims=True)
  scale = (inputs_max - inputs_min) / (max_value - min_value)
  return (inputs - inputs_min) / scale + min_value


class NonPeriodicConvGeneral(hk.Module):
  """General periodic convolution module."""

  def __init__(self,
               base_convolution: Callable[..., Any],
               output_channels: int,
               kernel_shape: Tuple[int, ...],
               rate: int = 1,
               name: str = 'periodic_conv_general',
               **conv_kwargs: Any):
    """Constructs NonPeriodicConvGeneral module similar to a Periodic one.

    Args:
      base_convolution: standard convolution module e.g. hk.Conv1D.
      output_channels: number of output channels.
      kernel_shape: shape of the kernel, compatible with `base_convolution`.
      rate: dilation rate of the convolution.
      name: name of the module.
      **conv_kwargs: additional arguments passed to `base_convolution`.
    """
    super().__init__(name=name)
    self._conv_module = base_convolution(
        output_channels=output_channels,
        kernel_shape=kernel_shape,
        padding='VALID',
        rate=rate,
        **conv_kwargs)

  def __call__(self, inputs):
    output = self._conv_module(jnp.expand_dims(inputs, axis=0))
    return jnp.squeeze(output, axis=0)


class NonPeriodicConv1D(NonPeriodicConvGeneral):
  """Periodic convolution module in 1D."""

  def __init__(self,
               output_channels: int,
               kernel_shape: Tuple[int, ...],
               rate: int = 1,
               name='periodic_conv_1d',
               **conv_kwargs):
    """Constructs PeriodicConv1D module."""
    super().__init__(
        base_convolution=hk.Conv1D,
        output_channels=output_channels,
        kernel_shape=kernel_shape,
        rate=rate,
        name=name,
        **conv_kwargs)


class PolynomialConstraint():
  """Module that parametrizes coefficients of polynomially accurate derivatives.

  Generates stencil coefficients that are guaranteed to approximate derivative
  of `derivative_orders[i]` along ith dimension with polynomial accuracy of
  `accuracy_order` order. The approximation is enforced by taking a linear
  superposition of the nullspace of linear constraints combined with a bias
  solution, which can be either specified directly using `bias` argument or
  generated automatically using `layers_util.polynomial_accuracy_coefficients`.
  """

  def __init__(
      self,
      stencils: Sequence[np.ndarray],
      derivative_orders: Tuple[int, ...],
      method: layers_util.Method,
      steps: Tuple[float, ...],
      accuracy_order: int = 1,
      bias_accuracy_order: int = 1,
      bias: Optional[Array] = None,
      precision: lax.Precision = lax.Precision.HIGHEST
  ):
    """Constructs the object.

    Args:
      stencils: sequence of 1d stencils, one per grid dimension
      derivative_orders: derivative orders along corresponding directions.
      method: discretization method (finite volumes or finite differences).
      steps: spatial separations between the adjacent cells.
      accuracy_order: order to which polynomial accuracy is enforced.
      bias_accuracy_order: integer order of polynomial accuracy to use for the
        bias term. Only used if bias is not provided.
      bias: np.ndarray of shape (grid_size,) to which zero-vectors will be
        mapped. Must satisfy polynomial accuracy to the requested order. By
        default, we use standard low-order coefficients for the given grid.
      precision: numerical precision for matrix multplication. Only relevant on
        TPUs.
    """
    self.precision = precision
    grid_steps = {*steps}
    if len(grid_steps) != 1:
      raise ValueError('nonuniform steps not supported by PolynomialConstraint')
    grid_step, = grid_steps
    #  stencil coefficients `c` satisfying `constraint_matrix @ c = rhs`
    #  satisfies polynomial accuracy constraint of the given order
    constraint_matrix, rhs = layers_util.polynomial_accuracy_constraints(
        stencils, method, derivative_orders, accuracy_order, grid_step)

    if bias is None:
      bias_grid = layers_util.polynomial_accuracy_coefficients(
          stencils, method, derivative_orders, bias_accuracy_order, grid_step)
      bias = bias_grid.ravel()

    self.bias = bias
    norm = np.linalg.norm(np.dot(constraint_matrix, bias) - rhs)
    if norm > 1e-8:
      raise ValueError('invalid bias, not in nullspace')

    # https://en.wikipedia.org/wiki/Kernel_(linear_algebra)#Nonhomogeneous_systems_of_linear_equations
    _, _, v = np.linalg.svd(constraint_matrix)
    nullspace_size = constraint_matrix.shape[1] - constraint_matrix.shape[0]
    if not nullspace_size:
      raise ValueError(
          'there is only one valid solution accurate to this order')

    # nullspace from the SVD is always normalized such that its singular values
    # are 1 or 0, which means it's actually independent of the grid spacing.
    self._nullspace_size = nullspace_size
    self.nullspace = v[-nullspace_size:]
    self.nullspace /= (grid_step**np.array(derivative_orders)).prod()

  @property
  def subspace_size(self) -> int:
    """Returns the size of the coefficients subspace with desired accuracy."""
    return self._nullspace_size

  def __call__(
      self,
      inputs: Array
  ) -> Array:
    """Returns polynomially accurate coefficients parametrized by `inputs`.

    Args:
      inputs: array whose last dimension represents linear superposition of
        valid polynomially accurate coefficients. Last dimension must be equal
        to `subspace_size`.

    Returns:
      array whose last dimension represents valid coefficients that approximate
      `derivate_orders` with polynomial accuracy on a stencil specified in
      `stencils` at position (0.,) * ndims.
    """
    return self.bias + jnp.tensordot(inputs, self.nullspace, axes=[-1, 0],
                                     precision=self.precision)


class StencilCoefficients():
  """Module that approximates stencil coefficients with polynomial accuracy.

  Generates stencil coefficients that approximate a spatial derivative of
  order `derivative_orders[i]` along i'th dimension by combining a trainable
  model generated by `tower_factory` and `PolynomilConstraint` layer.
  """

  def __init__(
      self,
      stencils: Sequence[np.ndarray],
      derivative_orders: Tuple[int, ...],
      tower_factory: Callable[[int], Callable[..., Any]],
      steps: Tuple[float, ...],
      method: layers_util.Method = layers_util.Method.FINITE_VOLUME,
      **kwargs: Any,
  ):
    """Constructs the object.

    Args:
      stencils: sequence of 1d stencils, one per grid dimension
      derivative_orders: derivative orders along corresponding directions.
      tower_factory: callable that constructs a neural network with specified
        number of output channels and the same spatial resolution as its inputs.
      steps: spatial separations between the adjacent cells.
      method: discretization method passed to PolynomialConstraint.
      **kwargs: additional arguments to be passed to PolynomialConstraint
        constructor.
    """
    self._polynomial_constraint = PolynomialConstraint(
        stencils, derivative_orders, method, steps, **kwargs)
    self._tower = tower_factory(self._polynomial_constraint.subspace_size)

  def __call__(self, inputs: Array, **kwargs) -> Array:
    """Returns coefficients approximating derivative conditioned on `inputs`."""
    parametrization = self._tower(inputs, **kwargs)
    return self._polynomial_constraint(parametrization)


class SpatialDerivativeFromLogits:
  """Module that transforms logits to polynomially accurate derivatives.

  Applies `PolynomialConstraint` layer to input logits and combines the
  resulting coefficients with basis. Compared to `SpatialDerivative`, this
  module does not compute `logits`, but takes them as an argument.
  """

  def __init__(
      self,
      stencil_shape: Tuple[int, ...],
      input_offset: Tuple[float, ...],
      target_offset: Tuple[float, ...],
      derivative_orders: Tuple[int, ...],
      steps: Tuple[float, ...],
      extract_patch_method: str = 'roll',
      tile_layout: Optional[Tuple[int, ...]] = None,
      method: layers_util.Method = layers_util.Method.FINITE_VOLUME,
  ):
    self.stencil_shape = stencil_shape
    self.roll, shift = layers_util.get_roll_and_shift(
        input_offset, target_offset)
    stencils = layers_util.get_stencils(stencil_shape, shift, steps)
    self.constraint = PolynomialConstraint(
        stencils, derivative_orders, method, steps)
    self._extract_patch_method = extract_patch_method
    self.tile_layout = tile_layout

  @property
  def subspace_size(self) -> int:
    return self.constraint.subspace_size

  @property
  def stencil_size(self) -> int:
    return int(np.prod(self.stencil_shape))

  def _validate_logits(self, logits):
    if logits.shape[-1] != self.subspace_size:
      raise ValueError('The last dimension of `logits` did not match subspace '
                       f'size; {logits.shape[-1]} vs. {self.subspace_size}')

  def extract_patches(self, inputs):
    rolled = jnp.roll(inputs, self.roll)
    patches = layers_util.extract_patches(
        rolled, self.stencil_shape,
        self._extract_patch_method, self.tile_layout)
    return patches

  @functools.partial(jax.named_call, name='SpatialDerivativeFromLogits')
  def __call__(self, inputs, logits):
    self._validate_logits(logits)
    coefficients = self.constraint(logits)
    patches = self.extract_patches(inputs)
    return layers_util.apply_coefficients(coefficients, patches)


T = TypeVar('T')


def fuse_spatial_derivative_layers(
    derivatives: Dict[T, SpatialDerivativeFromLogits],
    all_logits: jnp.ndarray,
    *,
    constrain_with_conv: bool = False,
    fuse_patches: bool = False,
) -> Dict[T, Callable[[jnp.ndarray], jnp.ndarray]]:
  """Evaluate spatial derivatives by fusing together constraints.

  Despite the additional calculation, this can be faster on TPUs because the
  full block diagonal constraint matrix is small enough to fit within a 128x128
  matrix.

  Args:
    derivatives: mapping from key to SpatialDerivativeFromLogits.
    all_logits: stacked logits to use as input into spatial derivatives.
    constrain_with_conv: whether to constrain with a 1x1 convolution instead of
      direct matrix multiplication.
    fuse_patches: whether to also fuse the extraction of patches.

  Returns:
    Functions that when applied evaluate derivatives.
  """
  joint_bias = jnp.concatenate(
      [derivative.constraint.bias for derivative in derivatives.values()])
  joint_nullspace = scipy.linalg.block_diag(
      *[deriv.constraint.nullspace for deriv in derivatives.values()]
  )
  precision, = {deriv.constraint.precision for deriv in derivatives.values()}
  tile_layout, = {deriv.tile_layout for deriv in derivatives.values()}

  if constrain_with_conv:
    ndim = len(tile_layout)
    kernel = jnp.expand_dims(
        joint_nullspace.astype(np.float32), axis=tuple(range(ndim)))
    all_coefficients = joint_bias + layers_util.periodic_convolution(
        all_logits, kernel, tile_layout=tile_layout, precision=precision)
  else:
    if tile_layout is not None:
      all_logits = tiling.space_to_batch(all_logits, tile_layout)
    all_coefficients = joint_bias + jnp.tensordot(
        all_logits, joint_nullspace, axes=[-1, 0], precision=precision)
    if tile_layout is not None:
      all_coefficients = tiling.batch_to_space(all_coefficients, tile_layout)

  stencil_sizes = [deriv.stencil_size for deriv in derivatives.values()]
  coefficients_list = jnp.split(
      all_coefficients, np.cumsum(stencil_sizes), axis=-1)
  coefficients_map = dict(zip(derivatives, coefficients_list))

  stencil_shapes = [deriv.stencil_shape for k, deriv in derivatives.items()]
  for k, deriv in derivatives.items():
    if any(r != 0 for r in deriv.roll):
      raise ValueError(f'derivative {k} uses roll: {deriv.roll}')

  @functools.partial(jax.named_call, name='evaluate_derivatives')
  def evaluate(key, inputs):
    if fuse_patches:
      all_patches = layers_util.fused_extract_patches(
          inputs, stencil_shapes, tile_layout)
      all_terms = all_coefficients * all_patches
      split_terms = jnp.split(all_terms, np.cumsum(stencil_sizes), axis=-1)
      index = list(derivatives).index(key)
      return jnp.sum(split_terms[index], axis=-1, keepdims=True)
    else:
      patches = derivatives[key].extract_patches(inputs)
      return layers_util.apply_coefficients(coefficients_map[key], patches)

  return {k: functools.partial(evaluate, k) for k in derivatives}


class SpatialDerivative:
  """Module that learns spatial derivative with polynomial accuracy.

  Combines StencilCoefficients with extract_stencils to construct a trainable
  model that approximates spatial derivative.
  """

  def __init__(
      self,
      stencil_shape: Tuple[int, ...],
      input_offset: Tuple[float, ...],
      target_offset: Tuple[float, ...],
      derivative_orders: Tuple[int, ...],
      tower_factory: Callable[[int], Callable[..., Any]],
      steps: Tuple[float, ...],
      extract_patch_method: str = 'roll',
      tile_layout: Optional[Tuple[int, ...]] = None,
  ):
    self._stencil_shape = stencil_shape
    self._roll, self._shift = layers_util.get_roll_and_shift(
        input_offset, target_offset)
    stencils = layers_util.get_stencils(stencil_shape, self._shift, steps)
    self._coefficients_module = StencilCoefficients(
        stencils, derivative_orders, tower_factory, steps)
    self._extract_patch_method = extract_patch_method
    self._tile_layout = tile_layout

  @functools.partial(jax.named_call, name='SpatialDerivative')
  def __call__(self, inputs, *auxiliary_inputs):
    """Computes spatial derivative of `inputs` evaluated at `offset`."""
    # TODO(jamieas): consider moving this roll inside `extract_patches`. For the
    # `roll` implementation of `extract_patches`, we can simply add it to the
    # `shifts`. For the `conv` implementation, we may be able to effectively
    # roll the input by adjusting how arrays are padded.
    rolled = jnp.roll(inputs, self._roll)
    patches = layers_util.extract_patches(
        rolled, self._stencil_shape,
        self._extract_patch_method, self._tile_layout)
    if auxiliary_inputs is not None:
      auxiliary_inputs = [jnp.roll(aux, self._roll) for aux in auxiliary_inputs]
      rolled = jnp.concatenate([rolled, *auxiliary_inputs], axis=-1)
    coefficients = self._coefficients_module(rolled)
    return layers_util.apply_coefficients(coefficients, patches)
